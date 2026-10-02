"""Fetch immutable SWE-smith-py shards with their published LFS checksums.

Dataset rows contain protected mutation patches. Keep all raw data in private ignored
controller storage. This is inventory, not final curation or permission to run models.
"""

import argparse
import concurrent.futures
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from recursive_run.artifacts import digest

DATASET = "SWE-bench/SWE-smith-py"
REVISION = "77cab9055d42ab4a5c25c89a8f937096db13558e"
REPO = "swesmith/pygments__pygments.27649ebb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/dataset"))
    args = parser.parse_args()
    raw = args.output / "raw"
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    url = f"https://huggingface.co/api/datasets/{DATASET}/tree/{REVISION}/data?expand=false"
    with urllib.request.urlopen(url, timeout=30) as response:
        metadata = json.load(response)
    if len(metadata) != 11:
        raise ValueError(
            "unexpected shard membership; inspect pinned source before changing protocol"
        )

    def fetch(item):
        name = item["path"]
        if not re.fullmatch(r"data/train-\d{5}-of-00011\.parquet", name):
            raise ValueError("unexpected dataset filename")
        expected = item["lfs"]["oid"]
        size = item["size"]
        path = raw / Path(name).name
        if path.exists() and path.stat().st_size == size and digest(path.read_bytes()) == expected:
            return path
        temporary = path.with_suffix(".partial")
        h = hashlib.sha256()
        total = 0
        try:
            with (
                urllib.request.urlopen(
                    f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{name}",
                    timeout=45,
                ) as response,
                temporary.open("wb") as output,
            ):
                while chunk := response.read(1048576):
                    total += len(chunk)
                    if total > size:
                        raise ValueError("shard exceeded published size")
                    h.update(chunk)
                    output.write(chunk)
            if total != size or h.hexdigest() != expected:
                raise ValueError("shard integrity mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        print("Verified", path.name, flush=True)
        return path

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        paths = list(executor.map(fetch, metadata))
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
        "dataset": DATASET,
        "revision": REVISION,
        "repository": REPO,
        "total_dataset_rows": total,
        "repository_rows": len(rows),
        "shards": [
            {"path": x["path"], "size": x["size"], "sha256": x["lfs"]["oid"]} for x in metadata
        ],
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
