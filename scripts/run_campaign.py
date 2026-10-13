"""RL1 learning campaign (live). Refuses to start until every gate holds.

    uv run python scripts/run_campaign.py --check        # gate status only; no network
    uv run --extra execution --extra cloud python scripts/run_campaign.py \
        --ledger <path on verified durable storage>

Gates (src/recursive_run/live.py `learning_gates` plus `Manifest.blockers("learning")`):
learning authorized in configs/rl1-decisions.json, durable storage verified with a receipt,
every pool task human-reviewed, partition confirmed. Caps come from decision A
(`ledger_caps_micro_usd`); sandbox settings from configs/smoke-authorization.json.
A restart with the same ledger resumes; finished cells and consumed proposal slots are
never repeated. Final outcomes stay sealed until every final cell is terminal.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

from recursive_run.artifacts import digest

DECISIONS = Path("configs/rl1-decisions.json")
AUTH = Path("configs/smoke-authorization.json")
TEMPLATE = Path(".runtime/e2b-template-tools.json")
SHARDS = Path(".runtime/benchmarks/swe-smith-py/raw")
SOURCES = {"sqlglot": Path("/tmp/claude-0/src/sqlglot"), "pygments": Path("/tmp/claude-0/src/pyg")}
RELEASE_POLICY = b"final sealed until every final cell is terminal; report once; v1"


def load(args):
    return (
        json.loads(DECISIONS.read_text()),
        json.loads(Path(args.pool).read_text()),
        json.loads(Path(args.partition).read_text()),
    )


def manifest_for(args, decisions, partition, store, template, packing):
    from recursive_run.contracts import Manifest
    from recursive_run.providers import openai as oa
    from recursive_run.providers.e2b import E2BRunner

    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    receipts = [
        json.loads(Path(p).read_text())
        for p in [
            ".runtime/smoke/intake-receipts.json",
            *sorted(glob.glob(".runtime/curation/intake-*.json")),
        ]
    ]
    spec = partition["spec"]
    sources = "|".join(f"{p.repository}@{p.reference}" for p in _profiles())
    return Manifest(
        experiment_id=f"rl1-{args.campaign}",
        stage="RL1",
        mode="live",
        source_revision=sources,
        model_version=oa.MODEL,
        model_settings_hash=digest(
            f"{oa.MODEL}|t0|{oa.MAX_INPUT_TOKENS}|{oa.MAX_OUTPUT_TOKENS}".encode()
        ),
        adapter_revision=revision.stdout.strip(),
        image_digest=f"{template['base_image']}|e2b:{template['template_id']}:{template['build_id']}",
        runner_provider="E2B",
        expiry_verified=True,
        dispatch_protocol_hash=digest(
            (
                E2BRunner.capabilities.protocol_hash
                + "|"
                + oa.OpenAIProvider.capabilities.protocol_hash
            ).encode()
        ),
        prices_hash=digest(AUTH.read_bytes() + oa.PRICE_SOURCE.encode()),
        durable_storage_verified=decisions["durable_storage"]["verified"] is True,
        authentication_verified=True,
        execution_authorized=decisions["learning_authorized"] is True,
        preparation_limits_hash=digest(b"worker:60s:1MiB:2vcpu:2GiB:egress-blocked"),
        scoring_limits_hash=digest(b"worker:60s:1MiB:fresh-process-per-input"),
        oracle_hash=digest(
            json.dumps(
                {t: [x.decode() for x in store.get(t).expected] for t in sorted(store.families)}
            ).encode()
        ),
        intake_receipts_hash=digest(json.dumps(receipts, sort_keys=True).encode()),
        cost_policy_hash=digest(
            json.dumps(decisions["ledger_caps_micro_usd"], sort_keys=True).encode()
        ),
        smoke_passed=True,  # RL0 feasibility: docs/smoke-report.md (v1-v3)
        partition_hash=partition["spec_hash"],
        group_map_hash=spec["group_map_hash"],
        sampling_receipt_hash=digest(
            json.dumps(
                {
                    k: spec[k]
                    for k in (
                        "draw_method",
                        "seed",
                        "intended_population",
                        "sampling_justified_receipt",
                    )
                },
                sort_keys=True,
            ).encode()
        ),
        packing_hash=digest(json.dumps(packing, sort_keys=True).encode()),
        analysis_hash=digest(Path("docs/rl1-analysis-plan.md").read_bytes()),
        release_policy_hash=digest(RELEASE_POLICY),
    )


def _profiles():
    from recursive_run.repositories import PROFILES

    return [p for p in PROFILES.values() if p.admitted]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report gates and exit")
    parser.add_argument("--ledger", help="controller ledger on verified durable storage")
    parser.add_argument("--campaign", default="c1")
    parser.add_argument("--pool", default="configs/task-pool-v1.json")
    parser.add_argument("--partition", default="configs/partition-v1.json")
    args = parser.parse_args()

    from recursive_run.live import learning_gates

    decisions, pool, partition = load(args)
    gates = learning_gates(decisions, pool, partition)
    if args.check:
        print(json.dumps({"ready": not gates, "gates": gates}, indent=1))
        sys.exit(1 if gates else 0)
    if gates:
        raise SystemExit("blocked:\n- " + "\n- ".join(gates))
    if not args.ledger:
        raise SystemExit("--ledger is required for a live campaign")
    run(args, decisions, pool, partition)


def run(args, decisions, pool, partition):  # pragma: no cover - live only
    from recursive_run.campaign import Campaign
    from recursive_run.gateway import Gateway
    from recursive_run.ledger import Ledger
    from recursive_run.live import (
        LIVE_PACKING,
        LiveProposer,
        LiveRollouts,
        TaskStore,
        plan_from_partition,
        token_counter,
    )
    from recursive_run.providers import openai as oa
    from recursive_run.providers.e2b import E2BRunner, from_authorization

    template = json.loads(TEMPLATE.read_text())
    os.environ["RL_E2B_TEMPLATE"] = template["template_id"]
    spec, _, lifetimes = from_authorization(AUTH)
    caps = decisions["ledger_caps_micro_usd"]
    store = TaskStore.load(pool, Path(".runtime"), SHARDS, SOURCES)
    manifest = manifest_for(args, decisions, partition, store, template, LIVE_PACKING)
    if manifest.blockers("learning"):
        raise SystemExit(f"blocked: {manifest.blockers('learning')}")
    ledger = Ledger(
        Path(args.ledger),
        cap=caps["total"],
        final_protected=caps["final_protected"],
        category_caps={k: caps[k] for k in ("coding_model", "proposal", "infrastructure")},
    )
    out = Path(".runtime/rl1") / args.campaign
    out.mkdir(parents=True, exist_ok=True)
    try:
        templates = json.loads(Path("configs/smoke/templates.json").read_text())
        protocol = decisions["executor_protocol"]
        reuse_context = {
            "panel": partition["spec_hash"],
            "draw": str(partition["spec"]["seed"]),
            "model": oa.MODEL,
            "settings": manifest.model_settings_hash,
            "harness": digest(json.dumps([templates, protocol], sort_keys=True).encode()),
            "adapter": manifest.adapter_revision,
            "image": manifest.image_digest,
            "oracle": manifest.oracle_hash,
            "split": "selection",
            "limits": manifest.scoring_limits_hash,
            "assistance": "none",
        }
        plan = plan_from_partition(args.campaign, partition, pool, reuse_context)
        provider = oa.OpenAIProvider()
        provider.client.models.retrieve(oa.MODEL)  # free authentication and snapshot check
        gateway = Gateway(ledger, manifest, provider, maximum_output_bytes=65536)
        rollouts = LiveRollouts(
            ledger,
            E2BRunner(ledger, spec),
            gateway,
            manifest,
            store,
            templates,
            lifetimes,
            out / "miniswe-config",
            max_format_errors=protocol["max_consecutive_format_errors"],
        )
        campaign = Campaign(
            ledger,
            plan,
            rollouts,
            LiveProposer(provider, plan.proposal_cash),
            token_counter(),
            Path("configs/smoke/skill-S.md").read_bytes(),
        )
        bound = campaign._get("manifest")
        current = digest(manifest.model_dump_json().encode())
        if bound is None:
            campaign._put("manifest", current)
        elif bound != current:
            raise SystemExit("manifest changed since this campaign started; resume refused")
        report = campaign.run()
        (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps(campaign.progress()), "ledger", ledger.money())
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
