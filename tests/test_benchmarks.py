import hashlib
import io
import json
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from recursive_run.benchmarks import (
    SOURCES,
    BenchmarkSource,
    Shard,
    fetch,
    fresh_after,
    permitted,
    summarize,
)


def test_roles_bound_partitions():
    smith, rebench, verified = (
        SOURCES[x] for x in ["swe-smith-py", "swe-rebench", "swe-bench-verified"]
    )
    assert all(permitted(smith, p) for p in ["smoke", "discovery", "selection", "final"])
    assert permitted(rebench, "final")
    assert not any(permitted(rebench, p) for p in ["smoke", "discovery", "selection"])
    assert permitted(verified, "reference")
    assert not any(permitted(verified, p) for p in ["smoke", "discovery", "selection", "final"])
    assert not any(s.license_verified for s in SOURCES.values())


def parquet_bytes(table):
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def fake_source(parts):
    shards = tuple(
        Shard(path=f"data/part-{i}.parquet", size=len(b), sha256=hashlib.sha256(b).hexdigest())
        for i, b in enumerate(parts)
    )
    return BenchmarkSource(
        source_id="fake",
        dataset="owner/fake",
        revision="0" * 40,
        role="fresh-holdout-candidate",
        published_license=None,
        shards=shards,
        date_field="created_at",
        note="test",
    )


class Response(io.BytesIO):
    def __enter__(self):
        return self


def opener_for(source, parts, corrupt=None):
    listing = [{"path": s.path, "size": s.size, "lfs": {"oid": s.sha256}} for s in source.shards]

    def opener(url, timeout):
        if "/api/datasets/" in url:
            return Response(json.dumps(listing).encode())
        index = next(i for i, s in enumerate(source.shards) if url.endswith(s.path))
        data = parts[index]
        return Response(data[:-1] + b"x" if index == corrupt else data)

    return opener


def tables():
    first = pa.table(
        {
            "instance_id": ["a", "b"],
            "repo": ["one/x", "two/y"],
            "created_at": ["2024-01-02T00:00:00", "2024-07-01T00:00:00"],
        }
    )
    # Overlapping split: "b" repeats and must be counted once.
    second = pa.table(
        {
            "instance_id": ["b", "c"],
            "repo": ["two/y", "two/y"],
            "created_at": ["2024-07-01T00:00:00", "2024-08-09T00:00:00"],
        }
    )
    return [parquet_bytes(first), parquet_bytes(second)]


def test_fetch_verifies_and_summary_deduplicates(tmp_path):
    parts = tables()
    source = fake_source(parts)
    paths = fetch(source, tmp_path, opener_for(source, parts))
    receipt = summarize(source, paths)
    assert receipt["total_rows"] == 3
    assert receipt["repositories"] == {"two/y": 2, "one/x": 1}
    assert receipt["rows_by_month"] == {"2024-01": 1, "2024-07": 1, "2024-08": 1}
    assert receipt["permitted_partitions"] == ["final"]
    assert receipt["intake_accepted"] is False
    assert fresh_after(source, paths, "2024-06-30") == 2
    with pytest.raises(ValueError):
        fresh_after(source, paths, "June 2024")


def test_fetch_rejects_corrupt_shard_and_changed_listing(tmp_path):
    parts = tables()
    source = fake_source(parts)
    with pytest.raises(ValueError, match="integrity"):
        fetch(source, tmp_path, opener_for(source, parts, corrupt=1))
    assert not list(tmp_path.glob("*.partial")) and not (tmp_path / "part-1.parquet").exists()
    changed = fake_source([parts[0]])
    with pytest.raises(ValueError, match="pinned"):
        fetch(changed, tmp_path, opener_for(source, parts))


def test_source_requires_pinned_revision():
    with pytest.raises(ValueError):
        BenchmarkSource(
            source_id="x",
            dataset="o/x",
            revision="main",
            role="external-reference",
            published_license=None,
            shards=(Shard(path="data/a.parquet", size=1, sha256="0" * 64),),
            note="",
        )
