# Public benchmark sources

Three public datasets are pinned in `src/recursive_run/benchmarks.py`. Each has a role that limits which partitions it may supply. A pinned source is inventory only: no row is an admitted task until it has an intake receipt, newly authored private checks and a frozen family map (design v1.1 §6–7). Licenses below are as published on the dataset cards and remain unverified.

| Source | Pinned revision | Role | May supply | Published license |
|---|---|---|---|---|
| `SWE-bench/SWE-smith-py` | `77cab905…` | development supply | smoke, discovery, selection, final | MIT |
| `nebius/SWE-rebench` | `89cdfbab…` | fresh-holdout candidate | final only | CC-BY-4.0 (per-row `license_name` also present) |
| `SWE-bench/SWE-bench_Verified` | `78f471bf…` | external reference | reference only | not stated on the card |

External-reference rows never enter learning or the sealed S/B/R comparison. They exist only to place results next to published scores. A fresh-holdout candidate may only supply the final partition, so the proposer never sees its tasks.

## Fetching

```sh
uv run python scripts/fetch_benchmark_sources.py --cutoff 2024-06-01            # all sources
uv run python scripts/fetch_benchmark_sources.py swe-rebench --cutoff 2024-06-01
```

The script checks that the published shard listing still matches the pin, then verifies each shard's size and LFS SHA-256 before use. Raw shards and `inventory-receipt.json` files go to `.runtime/benchmarks/<source>/`, which is ignored. Receipts are outcome-blind: unique row counts per repository and per creation month, plus rows after the cutoff. Rows are deduplicated by `instance_id`, because SWE-rebench's `filtered` split is a subset of its `test` split.

## Inventory obtained (8 October 2026)

| Source | Unique rows | Repositories | Created after 2024-06-01 | Date range |
|---|---|---|---|---|
| SWE-smith-py | 50,908 | 131 | n/a (synthetic, undated) | n/a |
| SWE-rebench | 21,336 | 3,468 | 2,772 | 2014-04 to 2025-04 |
| SWE-bench Verified | 500 | 12 | 0 | 2013-01 to 2023-08 |

2024-06-01 is the published knowledge cutoff of the candidate executor `gpt-4.1-mini-2025-04-14`. Creation after the cutoff lowers exposure risk but does not prove absence from pretraining. This SWE-rebench revision ends in April 2025, so it offers no fresh rows for an executor with a later cutoff; that case needs a newer pinned release.

## Suggested second SWE-smith repositories

The current oracle compares exact lexer token output. Repositories whose behavior is also deterministic text in, text out can reuse that pattern with a new `OracleSpec` contract:

| Repository (SWE-smith id) | Rows | Exact-output behavior |
|---|---|---|
| `lepture__mistune.bf54ef67` | 729 | Markdown → HTML |
| `tobymao__sqlglot.036601ba` | 790 | SQL parse / transpile → SQL |
| `andialbrecht__sqlparse.e57923b3` | 459 | SQL formatting and tokens |
| `kayak__pypika.1c9646f0` | 745 | Query builder → SQL string |

Row counts are mutation branches, not independent families. Adding a repository still needs the per-repository steps already done for Pygments: mirror inventory, reverse-patch reconstruction, private inputs, grouping and intake receipts. `TaskSpec` currently admits only `pygments/lexers/` paths; widening it is a protocol revision, not a code default.

## Not done

- No row from these sources is admitted, partitioned or run.
- Licenses and attribution obligations are unreviewed.
- SWE-rebench and SWE-bench Verified need a scoring oracle based on their `FAIL_TO_PASS` / `PASS_TO_PASS` tests in per-task images. That evaluator does not exist yet, and their published tests are not newly authored private checks.
