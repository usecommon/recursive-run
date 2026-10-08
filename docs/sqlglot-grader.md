# sqlglot grader

sqlglot tasks use a `CallContract` (`src/recursive_run/oracle.py`) instead of the Pygments `LexerContract`. A contract names one reviewed pipeline and its allowlisted options. The isolated worker runs that pipeline on one private input per process and returns bounded inert JSON. The output is either `{"ok": [strings]}` or `{"error": "ExceptionClassName"}`. The trusted evaluator compares that output exactly, as it does for lexer tokens.

| Pipeline | Call | Options |
|---|---|---|
| `sqlglot.transpile` | `sqlglot.transpile(input, **options)` | `read`, `write`, `pretty`, `identify` |
| `sqlglot.optimize` | `optimize(parse_one(input, read=dialect), schema, dialect).sql(dialect, pretty)` | `dialect`, `schema`, `pretty` |
| `mistune.html` | `create_markdown(escape, hard_wrap, plugins)(input)` | `escape`, `hard_wrap`, `plugins` |

Raising an error is behavior too. If the reference raises `ParseError` on an input, a repair must raise it as well, and a broken version that starts raising fails the check. Only the exception class name crosses the boundary, never its message or traceback.

The repair scope for sqlglot is `sqlglot/**/*.py` except `__main__.py`. Worker snapshots carry `sqlglot/` and `LICENSE` only: no tests, history, `sqlglotrs` or golden patches. `TaskSpec(repository="sqlglot")` enforces the scope. mistune is profiled but `admitted=False`, so a mistune `TaskSpec` is rejected.

## Development probe (8 October 2026), not intake evidence

Four real single-file sqlglot mutations from the pinned dataset were checked with hand-written private inputs (kept in ignored `.runtime/`, not in the repository). The probe ran the exact worker code in a plain local subprocess, because this session has no Docker daemon. It is **not isolated** and does not count as intake.

| Mutation | Pipeline | Reference twice | Broken |
|---|---|---|---|
| `func_pm_ctrl_invert_if__4b9d2pe9` (optimizer/simplify.py) | optimize | PASS, PASS | FAIL, PASS |
| `func_pm_ctrl_invert_if__4ld5094d` (dialects/sqlite.py) | transpile | PASS, PASS | FAIL, FAIL |
| `func_pm_ctrl_invert_if__5liurb5h` (dialects/snowflake.py) | transpile | PASS, PASS | FAIL, FAIL |
| `func_pm_ctrl_invert_if__ayiljv2m` (dialects/snowflake.py) | transpile | PASS, PASS | FAIL (ParseError), FAIL (ParseError) |

Reference output was identical across reruns. Each broken version failed at least one distinguishing input. The first input tried for the simplify mutation did not reach the inverted branch, which shows that each task needs reviewed inputs and that some candidates will fail oracle eligibility.

## Remaining before sqlglot tasks are admitted

- Run reference/broken pairs through `OfflineDockerRunner` (or the live runner) with `import_root=""`. `scripts/validate_offline_pairs.py` still assumes lexer contracts and the Pygments snapshot.
- Write intake receipts: provenance, the reverse-patch chain, grouping review, and license (sqlglot is MIT per its LICENSE file; this is unreviewed).
- Freeze per-task `OracleSpec`s with newly authored distinguishing and regression inputs.
