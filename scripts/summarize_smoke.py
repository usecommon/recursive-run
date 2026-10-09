"""Shareable smoke summary from private .runtime/smoke results (no inputs, traces or patches)."""

import argparse
import json
from pathlib import Path

ROOT = Path(".runtime/smoke")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", choices=["v1", "v2"], default="v2")
    args = parser.parse_args()
    v1 = args.protocol == "v1"
    results = []
    for name in ["results-first.json", "results.json"] if v1 else ["results-v2.json"]:
        path = ROOT / name
        if path.exists():
            results += json.loads(path.read_text())
    latest = {}
    for record in results:  # one record per task; later runs supersede earlier ones
        latest[record["task_id"]] = record
    receipts = json.loads((ROOT / "intake-receipts.json").read_text())
    interrupted_path = ROOT / "results-interrupted.json"
    interrupted = (
        json.loads(interrupted_path.read_text()) if v1 and interrupted_path.exists() else []
    )
    rows, totals = [], {"coding_model": 0, "infrastructure": 0}
    for receipt in receipts:
        record = latest.get(receipt["task_id"])
        short = receipt["task_id"].rsplit(".", 1)[-1]
        if not record:
            rows.append(f"| `{short}` | {receipt.get('family', '')} | not run | | | | | |")
            continue
        for key in totals:
            totals[key] += record.get("cash", {}).get(key, 0)
        outcomes = record.get("oracle", {}).get("outcomes") or []
        rows.append(
            "| `{}` | {} | {} | {} | {} | {} | {:.1f} | ${:.4f} |".format(
                short,
                receipt.get("family", "").replace("sqlglot/", ""),
                record.get("state"),
                (record.get("claim") or {}).get("exit_status") or record.get("error", "")[:40],
                ", ".join(outcomes) or "not scored",
                record.get("model_steps"),
                record.get("seconds", 0),
                (
                    record.get("cash", {}).get("coding_model", 0)
                    + record.get("cash", {}).get("infrastructure", 0)
                )
                / 1e6,
            )
        )
    for record in interrupted:  # harness failures are reported, never hidden by a rerun
        for key in totals:
            totals[key] += record.get("cash", {}).get(key, 0)
        rows.append(
            "| `{}` (first try) | | INTERRUPTED | harness bug, see note | not recorded | {} | | ${:.4f} |".format(
                record["task_id"].rsplit(".", 1)[-1],
                record.get("model_steps"),
                sum(record.get("cash", {}).values()) / 1e6,
            )
        )
    succeeded = sum(1 for r in latest.values() if r.get("state") == "SUCCEEDED")
    summary = {
        "tasks": len(receipts),
        "attempted": len(latest),
        "interrupted_by_harness": len(interrupted),
        "verified_successes": succeeded,
        "model_cash_usd": totals["coding_model"] / 1e6,
        "attempt_infrastructure_usd": totals["infrastructure"] / 1e6,
    }
    table = "\n".join(
        [
            "| Task | Family | State | Agent exit | Hidden checks | Steps | Seconds | Cost |",
            "|---|---|---|---|---|---|---|---|",
            *rows,
        ]
    )
    print(json.dumps(summary, indent=2))
    print(table)
    (ROOT / f"summary-{args.protocol}.json").write_text(
        json.dumps({**summary, "table": table}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
