"""RL0 live smoke on sqlglot: intake in E2B, then one baseline (S) attempt per task.

Authorization: configs/smoke-authorization.json ($25 total, $2 E2B). Private inputs,
traces, patches and the ledger stay in .runtime/smoke/ (ignored). Stages:

    uv run --extra execution --extra cloud python scripts/run_smoke.py intake
    uv run --extra execution --extra cloud python scripts/run_smoke.py attempts

Mirror checkout of the pinned sqlglot reference is required (--source).
"""

import argparse
import glob
import json
import os
import subprocess
import time
import traceback
import uuid
from pathlib import Path

import pyarrow.parquet as pq

from recursive_run.artifacts import digest
from recursive_run.contracts import Manifest, State
from recursive_run.gateway import Gateway
from recursive_run.inventory import snapshot_files
from recursive_run.ledger import Ledger
from recursive_run.miniswe_bridge import AdmittedEnvironment, AdmittedModel, run_attempt
from recursive_run.oracle import CallContract
from recursive_run.providers import openai as oa
from recursive_run.providers.e2b import E2BCodingPort, E2BRunner, from_authorization
from recursive_run.repositories import PROFILES
from recursive_run.smoke import SmokeTask, _begin, _lease, export_patch, prepare, score
from recursive_run.supply import changed_files

ROOT = Path(".runtime/smoke")
AUTH = Path("configs/smoke-authorization.json")
PROFILE = PROFILES["sqlglot"]
SHARDS = Path(".runtime/benchmarks/swe-smith-py/raw")
STORAGE_WAIVER = (
    "Smoke only: SQLite ledger on the ephemeral cloud container disk; same-host single "
    "writer with process-crash tests passing, power-loss durability uncertified. Worst-case "
    "loss is bounded by the $25 smoke cap, E2B lifetimes and per-call model ceilings."
)
# Dataset statements that contradict reference behavior are replaced; see intake receipts.
AUTHORED_PROBLEMS = {
    "func_pm_ctrl_invert_if__4ld5094d": (
        "DATEDIFF with a NANOSECOND unit transpiles to SQLite without the nanosecond scale "
        'factor.\n\nExample: sqlglot.transpile("SELECT DATEDIFF(NANOSECOND, x, y)", '
        'read="snowflake", write="sqlite") returns CAST((JULIANDAY(y) - JULIANDAY(x)) AS '
        "INTEGER). Other units such as MINUTE or SECOND are scaled (for example * 1440.0). "
        "NANOSECOND should be scaled like the other sub-day units."
    ),
    "func_pm_ctrl_invert_if__tdrra8m5": (
        "BigQuery inline structs are generated incorrectly when writing DuckDB.\n\n"
        'Example: sqlglot.transpile("SELECT STRUCT(1 AS x, 2 AS y)", read="bigquery", '
        'write="duckdb") drops the field names, producing {1, 2} instead of a DuckDB '
        "struct literal with keys."
    ),
}


def load_tasks():
    spec = json.loads((ROOT / "inputs.json").read_text())
    rows = {}
    for path in sorted(glob.glob(str(SHARDS / "*.parquet"))):
        table = pq.read_table(
            path,
            columns=["instance_id", "repo", "patch", "problem_statement"],
            filters=[("repo", "=", PROFILE.repository)],
        )
        for row in table.to_pylist():
            rows[row["instance_id"].rsplit(".", 1)[-1]] = row
    tasks = []
    for short, c in spec.items():
        row = rows[short]
        authored = AUTHORED_PROBLEMS.get(short)
        tasks.append(
            SmokeTask(
                task_id=row["instance_id"],
                family=changed_files(row["patch"])[0],
                bug_patch=row["patch"],
                problem=authored or row["problem_statement"],
                problem_source="authored: dataset statement contradicted reference behavior"
                if authored
                else "dataset problem_statement",
                contract=CallContract(pipeline=c["pipeline"], options=c["options"]),
                inputs=(c["distinguishing"], c["regression"]),
            )
        )
    return tasks


def setup(args):
    template = json.loads(Path(".runtime/e2b-template.json").read_text())
    os.environ.setdefault("RL_E2B_TEMPLATE", template["template_id"])
    spec, caps, lifetimes = from_authorization(AUTH)
    ROOT.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(ROOT / "ledger.db", cap=25_000_000, category_caps=caps)
    runner = E2BRunner(ledger, spec)
    head = subprocess.run(
        ["git", "-C", str(args.source), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if head != PROFILE.reference:
        raise SystemExit("sqlglot checkout is not the pinned reference")
    reference = snapshot_files(args.source, PROFILE.reference, PROFILE)
    return ledger, runner, lifetimes, reference, template


def intake(args):
    ledger, runner, lifetimes, reference, _ = setup(args)
    receipts, frozen = [], {}
    try:
        for task in load_tasks():
            try:
                receipt, broken, expected = prepare(
                    runner, ledger, PROFILE, reference, task, lifetimes
                )
                frozen[task.task_id] = {
                    "broken_files": {
                        k: broken[k].decode() for k in broken if broken[k] != reference.get(k)
                    },
                    "expected": [x.decode() for x in expected],
                }
            except Exception as error:  # record and continue; nothing is retried
                receipt = {
                    "task_id": task.task_id,
                    "accepted_for_smoke": False,
                    "error": f"{type(error).__name__}: {error}"[:300],
                }
            receipts.append(receipt)
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
        (ROOT / "intake-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
        (ROOT / "frozen.json").write_text(json.dumps(frozen) + "\n")
        print("ledger", ledger.money(), "workers", ledger.occupied_workers())
        ledger.close()


def manifest_for(template, receipts, frozen):
    authorization = AUTH.read_bytes()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    return Manifest(
        experiment_id="smoke-sqlglot-v1",
        stage="RL0",
        mode="live",
        source_revision=PROFILE.reference,
        model_version=oa.MODEL,
        model_settings_hash=digest(
            f"{oa.MODEL}|t0|{oa.MAX_INPUT_TOKENS}|{oa.MAX_OUTPUT_TOKENS}".encode()
        ),
        adapter_revision=revision.strip(),
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
        prices_hash=digest(authorization + oa.PRICE_SOURCE.encode()),
        durable_storage_verified=False,
        storage_waiver=STORAGE_WAIVER,
        authentication_verified=True,
        execution_authorized=True,
        preparation_limits_hash=digest(b"worker:60s:1MiB:2vcpu:2GiB:egress-blocked"),
        scoring_limits_hash=digest(b"worker:60s:1MiB:fresh-process-per-input"),
        oracle_hash=digest(
            json.dumps({k: v["expected"] for k, v in sorted(frozen.items())}).encode()
        ),
        intake_receipts_hash=digest(json.dumps(receipts, sort_keys=True).encode()),
        cost_policy_hash=digest(authorization),
    )


def parse_or_format_error(payload: bytes):
    try:
        return oa.parse_agent_response(payload)
    except (ValueError, KeyError) as error:
        from minisweagent.exceptions import FormatError

        raise FormatError(
            {
                "role": "user",
                "content": f"Format error: {type(error).__name__}",
                "extra": {"cost": 0.0, "exit_status": "FormatError"},
            }
        )


def attempts(args):
    ledger, runner, lifetimes, reference, template = setup(args)
    receipts = json.loads((ROOT / "intake-receipts.json").read_text())
    frozen = json.loads((ROOT / "frozen.json").read_text())
    accepted = {r["task_id"] for r in receipts if r.get("accepted_for_smoke")}
    manifest = manifest_for(template, receipts, frozen)
    if manifest.blockers("smoke"):
        raise SystemExit(f"blocked: {manifest.blockers('smoke')}")
    provider = oa.OpenAIProvider()
    provider.client.models.retrieve(oa.MODEL)  # free authentication and snapshot check
    gateway = Gateway(ledger, manifest, provider, maximum_output_bytes=65536)
    skill = Path("configs/smoke/skill-S.md").read_text()
    templates = json.loads(Path("configs/smoke/templates.json").read_text())
    out = ROOT / "results.json"
    results = json.loads(out.read_text()) if out.exists() else []  # accumulate across runs
    try:
        for task in load_tasks():
            if task.task_id not in accepted:
                continue
            if args.only and not any(o in task.task_id for o in args.only):
                continue
            item = frozen[task.task_id]
            broken = {**reference, **{k: v.encode() for k, v in item["broken_files"].items()}}
            expected = [x.encode() for x in item["expected"]]
            attempt = f"smoke-{task.task_id.rsplit('.', 1)[-1]}-{uuid.uuid4().hex[:6]}"
            record = {"task_id": task.task_id, "attempt": attempt, "started": time.time()}
            _begin(
                ledger,
                attempt,
                task.task_id,
                lifetimes["coding"] + lifetimes["scoring"] + 300,
                manifest.experiment_id,
            )
            coding = None
            try:
                coding = _lease(runner, attempt, "coding", lifetimes["coding"])
                runner.stage(coding, broken)
                model = AdmittedModel(
                    ledger,
                    gateway,
                    attempt,
                    "smoke-v1",
                    0,
                    0,
                    oa.MAXIMUM_CALL_CASH,
                    "smoke",
                    oa.encode_request,
                    parse_or_format_error,
                )
                environment = AdmittedEnvironment(
                    ledger, E2BCodingPort(runner, coding), attempt, "smoke-v1", 0, 0, 0
                )
                outcome = run_attempt(
                    manifest,
                    "smoke",
                    model,
                    environment,
                    task.problem,
                    skill,
                    templates,
                    ROOT / "miniswe-config",
                )
                record["claim"] = {k: outcome["claim"].get(k) for k in ["exit_status"]}
                record["trace"] = outcome["trace"]
                patch, allowed, forbidden, scratch = export_patch(runner, coding, broken, PROFILE)
                record.update(
                    patch=patch.decode(), edited=allowed, forbidden=forbidden, scratch=scratch
                )
            except Exception as error:
                record["error"] = f"{type(error).__name__}: {error}"[:500]
                record["traceback"] = traceback.format_exc()[-2000:]
                patch, allowed, forbidden = b"", [], []
            finally:
                if coding:
                    runner.release(coding)
            state = ledger.attempt(attempt)["state"]
            try:
                if state == "RUNNING" and ledger.occupied_workers() == 0:
                    ledger.transition(attempt, State.EVALUATING, attempt + "-evaluate")
                    result = score(
                        runner,
                        ledger,
                        attempt,
                        broken,
                        patch,
                        allowed,
                        task,
                        expected,
                        lifetimes,
                        forbidden,
                    )
                    record["oracle"] = result.model_dump()
                    ledger.transition(
                        attempt,
                        State.SUCCEEDED if result.success else State.FAILED,
                        attempt + "-terminal",
                        oracle=result,
                    )
            except Exception as error:  # keep the record; the attempt stays unresolved
                record["scoring_error"] = f"{type(error).__name__}: {error}"[:500]
            record["state"] = ledger.attempt(attempt)["state"]
            record["seconds"] = round(time.time() - record["started"], 1)
            record["model_steps"] = ledger.attempt(attempt)["steps"]
            record["cash"] = ledger.db.execute(
                "SELECT category, SUM(COALESCE(actual, maximum)) FROM obligations WHERE attempt=? GROUP BY category",
                (attempt,),
            ).fetchall()
            record["cash"] = {c: v for c, v in record["cash"]}
            results.append(record)
            out.write_text(json.dumps(results, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        k: record.get(k)
                        for k in [
                            "task_id",
                            "state",
                            "claim",
                            "edited",
                            "forbidden",
                            "seconds",
                            "model_steps",
                            "cash",
                            "error",
                        ]
                    }
                    | {"outcomes": record.get("oracle", {}).get("outcomes")}
                ),
                flush=True,
            )
    finally:
        print("ledger", ledger.money(), "workers", ledger.occupied_workers())
        ledger.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["intake", "attempts"])
    parser.add_argument("--source", type=Path, default=Path("/tmp/claude-0/src/sqlglot"))
    parser.add_argument("--only", nargs="*", help="substring filter on task ids")
    args = parser.parse_args()
    {"intake": intake, "attempts": attempts}[args.stage](args)


if __name__ == "__main__":
    main()
