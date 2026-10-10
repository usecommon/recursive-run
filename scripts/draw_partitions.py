"""Outcome-blind partition draw over the accepted task pool (configs/task-pool-v1.json).

Smoke keeps the 8 smoke families. The other 80 tasks are drawn into discovery 24,
selection 16 and final 40, stratified by repository so each partition has the same
sqlglot/Pygments mix (16/64 overall). The seed is derived from the pool hash, which was
fixed before any learning outcome exists. The result is PROVISIONAL until human review
of the pool is recorded; a redraw after review must state its reason.
"""

import json
import random
from pathlib import Path

from recursive_run.artifacts import digest
from recursive_run.repositories import PROFILES
from recursive_run.tasks import PartitionSpec

POOL = Path("configs/task-pool-v1.json")
OUT = Path("configs/partition-v1.json")
SIZES = {"discovery": 24, "selection": 16, "final": 40}
SUBSTITUTION_POLICY = (
    "No substitution after any outcome on a partition is visible. Before that, a task that "
    "fails human review is replaced by an unused accepted task from the same repository, drawn "
    "with the same seed stream; final tasks are never replaced in response to arm performance."
)


def main():
    pool = json.loads(POOL.read_text())
    tasks = pool["tasks"]
    smoke = sorted(t["task_id"] for t in tasks if t["source"] == "smoke-sqlglot")
    rest = sorted((t for t in tasks if t["source"] != "smoke-sqlglot"), key=lambda t: t["task_id"])
    seed = int(pool["pool_hash"][:16], 16)
    rng = random.Random(seed)
    strata = {}
    for t in rest:
        strata.setdefault(t["task_id"].split(".", 1)[0], []).append(t["task_id"])
    total = len(rest)
    parts = {name: [] for name in SIZES}
    for repo, ids in sorted(strata.items()):
        rng.shuffle(ids)
        # Proportional allocation; the largest stratum absorbs rounding.
        counts = {name: round(size * len(ids) / total) for name, size in SIZES.items()}
        cursor = 0
        for name in SIZES:
            parts[name] += ids[cursor : cursor + counts[name]]
            cursor += counts[name]
    # Fix rounding drift deterministically from the largest stratum's leftovers.
    used = {i for ids in parts.values() for i in ids}
    leftovers = sorted(i for t in rest if (i := t["task_id"]) not in used)
    rng.shuffle(leftovers)
    for name, size in SIZES.items():
        while len(parts[name]) > size:
            leftovers.append(parts[name].pop())
        while len(parts[name]) < size:
            parts[name].append(leftovers.pop())
    families = {t["task_id"]: t["family"] for t in tasks}
    spec = PartitionSpec(
        revision="partition-v1-provisional",
        source_revision="|".join(
            f"{p.repository}@{p.reference}" for p in (PROFILES["sqlglot"], PROFILES["pygments"])
        ),
        group_map_hash=digest(json.dumps(families, sort_keys=True).encode()),
        eligibility_receipts_hash=pool["pool_hash"],
        draw_method="random",
        seed=seed,
        intended_population=(
            "Oracle-observable single-file mutation repairs (procedural, LM-rewritten and "
            "mirrored-PR origins) in pinned sqlglot and Pygments, one task per outcome-blind "
            "independent family."
        ),
        sampling_justified_receipt=None,
        substitution_policy_hash=digest(SUBSTITUTION_POLICY.encode()),
        smoke=tuple(smoke),
        discovery=tuple(sorted(parts["discovery"])),
        selection=tuple(sorted(parts["selection"])),
        final=tuple(sorted(parts["final"])),
        task_families=families,
    )
    mix = {
        name: {
            repo: sum(1 for i in getattr(spec, name) if i.startswith(repo))
            for repo in ("tobymao", "pygments")
        }
        for name in ("smoke", "discovery", "selection", "final")
    }
    record = {
        "status": "PROVISIONAL: binding only after human review of the task pool is recorded",
        "substitution_policy": SUBSTITUTION_POLICY,
        "repository_mix": mix,
        "spec": json.loads(spec.model_dump_json()),
        "spec_hash": digest(spec.model_dump_json().encode()),
    }
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({"seed": seed, "mix": mix, "spec_hash": record["spec_hash"][:16]}))


if __name__ == "__main__":
    main()
