"""Write the outcome-blind family supply receipt and, when receipts exist, the checkpoint.

Inputs: a reference checkout of a pinned SWE-smith mirror (parsed, never imported) and
the checksum-verified SWE-smith-py shards from scripts/fetch_benchmark_sources.py.
Effort receipts are a JSON list of EffortReceipt records written by the reviewer.
"""

import argparse
import json
import subprocess
from pathlib import Path

from recursive_run.benchmarks import SOURCES, rows
from recursive_run.supply import PROFILES, EffortReceipt, changed_files, checkpoint, supply_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", choices=sorted(PROFILES), default="pygments")
    parser.add_argument("--source", type=Path, required=True, help="pinned mirror checkout")
    parser.add_argument("--shards", type=Path, default=Path(".runtime/benchmarks/swe-smith-py/raw"))
    parser.add_argument("--receipts", type=Path, help="effort receipts JSON list")
    parser.add_argument("--effort-limit-hours", type=float)
    parser.add_argument("--single-file-only", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    head = subprocess.run(
        ["git", "-C", str(args.source), "rev-parse", "HEAD"], capture_output=True, check=True
    )
    profile = PROFILES[args.repository]
    if head.stdout.decode().strip() != profile.reference:
        raise SystemExit("source checkout is not the pinned reference commit")
    files = {
        name: path.read_bytes()
        for path in args.source.rglob("*.py")
        if path.is_file()
        and not path.is_symlink()
        and profile.in_scope(name := path.relative_to(args.source).as_posix())
    }
    source = SOURCES["swe-smith-py"]
    paths = [args.shards / Path(s.path).name for s in source.shards]
    selected = [
        r
        for r in rows(source, paths, ["repo", "patch"])
        if r["repo"] == profile.repository
        and (not args.single_file_only or len(changed_files(r["patch"])) == 1)
    ]
    report = supply_report(selected, files, profile)
    report["single_file_only"] = args.single_file_only
    if args.receipts:
        if args.effort_limit_hours is None:
            raise SystemExit("freeze --effort-limit-hours before applying the checkpoint")
        receipts = [EffortReceipt(**r) for r in json.loads(args.receipts.read_text())]
        decision = checkpoint(receipts, report["independent_supply"], args.effort_limit_hours)
        report["checkpoint"] = decision.model_dump()
    else:
        report["checkpoint"] = {
            "decision": "BLOCKED",
            "reasons": ["effort receipts for the first eight families are not recorded"],
        }
    args.output = args.output or Path(f".runtime/family-supply-{args.repository}.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    summary = {
        k: report[k]
        for k in ["structural_candidates", "provisional_families", "independent_supply"]
    }
    print(json.dumps({**summary, "checkpoint": report["checkpoint"]["decision"]}))


if __name__ == "__main__":
    main()
