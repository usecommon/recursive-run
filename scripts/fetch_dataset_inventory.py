"""Fetch immutable SWE-smith-py shards with their published LFS checksums.

Dataset rows contain protected mutation patches. Keep all raw data in private ignored
controller storage. This is inventory, not final curation or permission to run models.
"""

import argparse
import json
from pathlib import Path

from recursive_run.benchmarks import SOURCES, fetch

REPO = "swesmith/pygments__pygments.27649ebb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/dataset"))
    args = parser.parse_args()
    raw = args.output / "raw"
    source = SOURCES["swe-smith-py"]
    paths = fetch(source, raw)
    import pyarrow.parquet as pq

    rows = []
    total = 0
    for path in paths:
        # Read bounded record batches; never execute source/dataset code in the controller.
        for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
            for row in batch.to_pylist():
                total += 1
                if row.get("repo") == REPO:
                    rows.append(row)
    receipt = {
        "schema_version": 1,
        "dataset": source.dataset,
        "revision": source.revision,
        "repository": REPO,
        "total_dataset_rows": total,
        "repository_rows": len(rows),
        "shards": [s.model_dump(exclude={"schema_version"}) for s in source.shards],
        "shard_checksums_verified": True,
        "intake_accepted": False,
        "eligible_families": None,
    }
    (args.output / "pygments-rows.json").write_text(
        json.dumps(rows, sort_keys=True, ensure_ascii=False) + "\n"
    )
    (args.output / "source-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "shards"}, indent=2))


if __name__ == "__main__":
    main()
