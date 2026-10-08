"""Fetch pinned public benchmark sources and write outcome-blind inventory receipts.

Raw rows contain protected golden patches and tests. Keep them in ignored controller
storage. A receipt is inventory only; it admits no task and authorizes no model spending.
"""

import argparse
import json
from pathlib import Path

from recursive_run.benchmarks import SOURCES, fetch, fresh_after, summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", help=f"default: all of {sorted(SOURCES)}")
    parser.add_argument("--output", type=Path, default=Path(".runtime/benchmarks"))
    parser.add_argument("--cutoff", help="executor training cutoff, YYYY-MM-DD")
    args = parser.parse_args()
    unknown = set(args.sources) - set(SOURCES)
    if unknown:
        parser.error(f"unknown sources: {sorted(unknown)}")
    for source_id in args.sources or sorted(SOURCES):
        source = SOURCES[source_id]
        paths = fetch(source, args.output / source_id / "raw")
        receipt = summarize(source, paths)
        if args.cutoff and source.date_field:
            receipt["cutoff"] = args.cutoff
            receipt["rows_after_cutoff"] = fresh_after(source, paths, args.cutoff)
        (args.output / source_id / "inventory-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        top = dict(list(receipt["repositories"].items())[:10])
        print(json.dumps({"source": source_id, "rows": receipt["total_rows"], "top": top}))


if __name__ == "__main__":
    main()
