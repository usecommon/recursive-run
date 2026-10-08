"""Pinned public benchmark sources and the partitions each may supply.

A source listed here is inventory, not admitted tasks. Rows still need intake receipts,
private oracle checks and family grouping before any partition can use them. Licenses are
recorded as published on the dataset card and remain unverified until reviewed.
"""

import hashlib
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable, Literal

from pydantic import Field, model_validator

from recursive_run.artifacts import digest
from recursive_run.contracts import Record

Role = Literal["development-supply", "fresh-holdout-candidate", "external-reference"]
Partition = Literal["smoke", "discovery", "selection", "final", "reference"]

# External-reference rows never reach learning or the sealed comparison; a fresh-holdout
# candidate may only supply the final partition, so its tasks stay unseen by the proposer.
PERMITTED: dict[str, frozenset[str]] = {
    "development-supply": frozenset({"smoke", "discovery", "selection", "final"}),
    "fresh-holdout-candidate": frozenset({"final"}),
    "external-reference": frozenset({"reference"}),
}


class Shard(Record):
    path: str = Field(pattern=r"^data/[A-Za-z0-9_.-]+\.parquet$")
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BenchmarkSource(Record):
    source_id: str = Field(pattern=r"^[a-z0-9-]+$")
    dataset: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    role: Role
    published_license: str | None
    license_verified: Literal[False] = False
    shards: tuple[Shard, ...]
    id_field: str = "instance_id"
    repository_field: str = "repo"
    date_field: str | None = None
    note: str

    @model_validator(mode="after")
    def unique_shards(self):
        if not self.shards or len({s.path for s in self.shards}) != len(self.shards):
            raise ValueError("nonempty unique shard list required")
        return self


def permitted(source: BenchmarkSource, partition: Partition) -> bool:
    return partition in PERMITTED[source.role]


SOURCES: dict[str, BenchmarkSource] = {
    s.source_id: s
    for s in [
        BenchmarkSource(
            source_id="swe-smith-py",
            dataset="SWE-bench/SWE-smith-py",
            revision="77cab9055d42ab4a5c25c89a8f937096db13558e",
            role="development-supply",
            published_license="mit",
            shards=tuple(
                Shard(path=f"data/train-{i:05d}-of-00011.parquet", size=size, sha256=sha)
                for i, (size, sha) in enumerate(
                    [
                        (
                            16852671,
                            "302a383bb9229c42063ff2364b2ea637e9e7a20d82e0d566614d3aba9234833d",
                        ),
                        (
                            5574116,
                            "f15e5b8ab49866e93245a736056fab680d6dde8fb5c2ba89c06241cf12a32afe",
                        ),
                        (
                            6006895,
                            "67512898124c03132ae5bbf78866610571a565724dcb6a2064009db8a6825613",
                        ),
                        (
                            117625772,
                            "cc4c9812480faac2bc2de49e78a271d17cc8dcd89a8af2268246c3c8632fd27d",
                        ),
                        (
                            9822409,
                            "6110c76e568c0ee03eb1c86d208887154c6406237f532436cf72ed2c265073ca",
                        ),
                        (
                            7434902,
                            "517420b48132eda3c073e6b9bc5fecbaf34c3dea7f3fec49a46f8615584f8200",
                        ),
                        (
                            12282590,
                            "8e55de0daa541c031779f17d6ef04ebc3a4e2caad708d3137edb4587d89e60f3",
                        ),
                        (
                            6665621,
                            "c558fc3b6507f36ae520a8b23ac0cd71be7d7c8bb15c0b09060fe18d83d21045",
                        ),
                        (
                            21661282,
                            "b861958c450293ee717c00659958d2f334a697edf804f33c10c75bd05b8787e7",
                        ),
                        (
                            7641038,
                            "20923272dab2706c1e07e9e11a19373c5c642efbb594a3b1c104440cf8cfc132",
                        ),
                        (
                            9078471,
                            "3b7c09dfa07209f550f210344caac267c2ac5ea9c6eefa84c67845ecdff0b6df",
                        ),
                    ]
                )
            ),
            note="Synthetic mutations; additional repositories need their own oracle contract.",
        ),
        BenchmarkSource(
            source_id="swe-rebench",
            dataset="nebius/SWE-rebench",
            revision="89cdfbab4ab1bd8f5a658bb212d1b63624f4f881",
            role="fresh-holdout-candidate",
            published_license="cc-by-4.0",
            shards=(
                Shard(
                    path="data/filtered-00000-of-00001.parquet",
                    size=33886640,
                    sha256="b2f037217a849732bf2e9b9d2b59d11561d03b4191a55c3142e1e58248817cfa",
                ),
                Shard(
                    path="data/test-00000-of-00002.parquet",
                    size=101556119,
                    sha256="39d4791f12cf5ee2a2e56d47eeef559642a800534ff053e1ae3acab0a0c87067",
                ),
                Shard(
                    path="data/test-00001-of-00002.parquet",
                    size=109723276,
                    sha256="c50af8bffbfe70fc3a89b2e47825f299bc04c1058318fe2263b7e4857f8193d7",
                ),
            ),
            date_field="created_at",
            note="Real issues; only rows created after the executor's training cutoff count as fresh.",
        ),
        BenchmarkSource(
            source_id="swe-bench-verified",
            dataset="SWE-bench/SWE-bench_Verified",
            revision="78f471bf655a3137b2e8a75af1501690ec009ec3",
            role="external-reference",
            published_license=None,
            shards=(
                Shard(
                    path="data/test-00000-of-00001.parquet",
                    size=6304616,
                    sha256="030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25",
                ),
            ),
            date_field="created_at",
            note="Comparison with published scores only; likely exposed in pretraining.",
        ),
    ]
}

Opener = Callable[..., object]


def fetch(source: BenchmarkSource, raw: Path, opener: Opener = urllib.request.urlopen):
    """Download pinned shards, verifying the published listing, size and LFS checksum."""
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = f"https://huggingface.co/api/datasets/{source.dataset}/tree/{source.revision}/data"
    with opener(base + "?expand=false", timeout=30) as response:
        listing = json.load(response)
    published = {
        (x["path"], x["size"], x["lfs"]["oid"]) for x in listing if x["path"].endswith(".parquet")
    }
    if published != {(s.path, s.size, s.sha256) for s in source.shards}:
        raise ValueError("published shards differ from the pinned source; revise the pin")
    paths = []
    for shard in source.shards:
        path = raw / Path(shard.path).name
        if not (
            path.exists()
            and path.stat().st_size == shard.size
            and digest(path.read_bytes()) == shard.sha256
        ):
            _download(source, shard, path, opener)
        paths.append(path)
    return paths


def _download(source: BenchmarkSource, shard: Shard, path: Path, opener: Opener):
    url = f"https://huggingface.co/datasets/{source.dataset}/resolve/{source.revision}/{shard.path}"
    temporary = path.with_suffix(".partial")
    h = hashlib.sha256()
    total = 0
    try:
        with opener(url, timeout=45) as response, temporary.open("wb") as output:
            while chunk := response.read(1048576):
                total += len(chunk)
                if total > shard.size:
                    raise ValueError("shard exceeded published size")
                h.update(chunk)
                output.write(chunk)
        if total != shard.size or h.hexdigest() != shard.sha256:
            raise ValueError("shard integrity mismatch")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def rows(source: BenchmarkSource, paths: list[Path], columns: list[str]):
    """Yield each instance once; published splits may overlap (SWE-rebench filtered ⊂ test)."""
    # Read bounded record batches; never execute source/dataset code in the controller.
    import pyarrow.parquet as pq

    seen = set()
    for path in paths:
        parquet = pq.ParquetFile(path)
        wanted = [source.id_field, *columns]
        if not set(wanted) <= set(parquet.schema_arrow.names):
            raise ValueError("pinned shard lacks a required column")
        for batch in parquet.iter_batches(batch_size=256, columns=wanted):
            for row in batch.to_pylist():
                if row[source.id_field] not in seen:
                    seen.add(row[source.id_field])
                    yield row


def summarize(source: BenchmarkSource, paths: list[Path]) -> dict:
    """Outcome-blind inventory: row counts per repository and per creation month."""
    columns = [source.repository_field] + ([source.date_field] if source.date_field else [])
    repositories = Counter()
    months = Counter()
    total = 0
    for row in rows(source, paths, columns):
        total += 1
        repositories[row.get(source.repository_field)] += 1
        if source.date_field and row.get(source.date_field):
            months[str(row[source.date_field])[:7]] += 1
    return {
        "schema_version": 1,
        "source_id": source.source_id,
        "dataset": source.dataset,
        "revision": source.revision,
        "role": source.role,
        "permitted_partitions": sorted(PERMITTED[source.role]),
        "published_license": source.published_license,
        "license_verified": False,
        "shards": [s.model_dump(exclude={"schema_version"}) for s in source.shards],
        "shard_checksums_verified": True,
        "total_rows": total,
        "repositories": dict(sorted(repositories.items(), key=lambda x: (-x[1], str(x[0])))),
        "rows_by_month": dict(sorted(months.items())),
        "intake_accepted": False,
        "eligible_families": None,
    }


def fresh_after(source: BenchmarkSource, paths: list[Path], cutoff: str) -> int:
    """Count rows created strictly after an ISO date; freshness is not eligibility."""
    if not source.date_field or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff):
        raise ValueError("dated source and ISO cutoff required")
    return sum(
        1
        for row in rows(source, paths, [source.date_field])
        if row.get(source.date_field) and str(row[source.date_field])[:10] > cutoff
    )
