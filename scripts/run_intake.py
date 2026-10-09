"""Curation intake in E2B preparation sandboxes for a batch of drafted tasks.

Inputs (private, ignored): .runtime/curation/inputs-<repo>.json mapping a dataset
instance suffix to {pipeline, options, distinguishing, regression}. Writes intake
receipts and frozen reference outputs next to it. Spend goes to a separate curation
ledger capped at $1 of infrastructure; no model is called.

    uv run --extra execution --extra cloud python scripts/run_intake.py sqlglot
"""

import argparse
import glob
import json
import os
import subprocess
from pathlib import Path

import pyarrow.parquet as pq

from recursive_run.inventory import snapshot_files
from recursive_run.ledger import Ledger
from recursive_run.oracle import CallContract
from recursive_run.providers.e2b import E2BRunner, from_authorization
from recursive_run.repositories import PROFILES
from recursive_run.smoke import SmokeTask, prepare
from recursive_run.supply import changed_files

ROOT = Path(".runtime/curation")
SHARDS = Path(".runtime/benchmarks/swe-smith-py/raw")
SOURCES = {"sqlglot": Path("/tmp/claude-0/src/sqlglot")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", choices=sorted(SOURCES))
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    profile = PROFILES[args.repository]
    source = args.source or SOURCES[args.repository]
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if head != profile.reference:
        raise SystemExit("checkout is not the pinned reference")
    template = json.loads(Path(".runtime/e2b-template-tools.json").read_text())
    os.environ["RL_E2B_TEMPLATE"] = template["template_id"]
    spec, _, lifetimes = from_authorization(Path("configs/smoke-authorization.json"))
    ledger = Ledger(
        ROOT / "ledger.db",
        cap=1_000_000,
        category_caps={"coding_model": 0, "proposal": 0, "infrastructure": 1_000_000},
    )
    runner = E2BRunner(ledger, spec)
    reference = snapshot_files(source, profile.reference, profile)
    drafts = json.loads((ROOT / f"inputs-{args.repository}.json").read_text())
    rows = {}
    for path in sorted(glob.glob(str(SHARDS / "*.parquet"))):
        table = pq.read_table(
            path,
            columns=["instance_id", "repo", "patch", "problem_statement"],
            filters=[("repo", "=", profile.repository)],
        )
        for row in table.to_pylist():
            rows[row["instance_id"].rsplit(".", 1)[-1]] = row
    receipts_path = ROOT / f"intake-{args.repository}.json"
    frozen_path = ROOT / f"frozen-{args.repository}.json"
    receipts = json.loads(receipts_path.read_text()) if receipts_path.exists() else []
    frozen = json.loads(frozen_path.read_text()) if frozen_path.exists() else {}
    done = {r["task_id"] for r in receipts}
    try:
        for short, draft in drafts.items():
            row = rows[short]
            if row["instance_id"] in done:
                continue
            task = SmokeTask(
                task_id=row["instance_id"],
                family=changed_files(row["patch"])[0],
                bug_patch=row["patch"],
                problem=row["problem_statement"],
                problem_source="dataset problem_statement (unreviewed)",
                contract=CallContract(pipeline=draft["pipeline"], options=draft["options"]),
                inputs=(draft["distinguishing"], draft["regression"]),
            )
            try:
                receipt, broken, expected = prepare(
                    runner,
                    ledger,
                    profile,
                    reference,
                    task,
                    lifetimes,
                    phase="intake",
                    experiment=f"curation-{args.repository}",
                )
                frozen[task.task_id] = {
                    "broken_files": {
                        k: broken[k].decode() for k in broken if broken[k] != reference.get(k)
                    },
                    "expected": [x.decode() for x in expected],
                }
            except Exception as error:  # recorded, never retried here
                receipt = {
                    "task_id": task.task_id,
                    "family": task.family,
                    "accepted_for_smoke": False,
                    "error": f"{type(error).__name__}: {error}"[:300],
                }
            receipt["reviewer"] = "claude (automated draft; human review pending)"
            receipts.append(receipt)
            receipts_path.write_text(json.dumps(receipts, indent=2) + "\n")
            frozen_path.write_text(json.dumps(frozen) + "\n")
            print(
                json.dumps(
                    {
                        k: receipt.get(k)
                        for k in [
                            "task_id",
                            "reverse_chain_verified",
                            "reference_passed_twice",
                            "broken_outcomes",
                            "accepted_for_smoke",
                            "error",
                        ]
                    }
                ),
                flush=True,
            )
    finally:
        print("ledger", ledger.money(), "workers", ledger.occupied_workers())
        ledger.close()


if __name__ == "__main__":
    main()
