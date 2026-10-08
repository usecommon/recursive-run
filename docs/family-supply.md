# Family supply checkpoint

Design v1.1 §6 requires a checkpoint after the first eight offline families. Record the hours per admitted family, the rejection rate and the remaining independent supply, freeze an effort limit, then decide to proceed, stop, or freeze a smaller descriptive protocol. `src/recursive_run/supply.py` and `scripts/family_supply_checkpoint.py` implement it.

```sh
uv run python scripts/fetch_benchmark_sources.py swe-smith-py
uv run python scripts/family_supply_checkpoint.py --source <pinned pygments mirror checkout> \
    [--single-file-only] [--receipts effort.json --effort-limit-hours N]
```

The script refuses a checkout that is not the pinned reference commit `d53d06ab…`. It parses lexer modules with `ast` and never imports them.

## Grouping rule (provisional, v1)

- A candidate edits only `pygments/lexers/*.py`. Edits to the registry (`__init__.py`, `_mapping.py`) are rejected.
- Files edited together by one combined mutation form one family (a combined task inherits every component's relationships).
- Two families conflict when a module in one directly imports a module in the other. Independent supply is the largest conflict-free set of families. A shared importer such as `templates.py` conflicts with what it imports, but it does not chain those imported lexers into one family.

This rule is outcome-blind and still needs reviewer sign-off (`grouping_reviewed: false`). It ignores near-duplicate code across files and other relationships not visible in imports.

## Result (8 October 2026, pinned SWE-smith-py revision 77cab905…)

| Candidate pool | Structural candidates | Families | Independent supply | Required |
|---|---|---|---|---|
| All lexer-only mutations | 934 | 86 | **73** | 88 |
| Single-file mutations only | 890 | 96 | **82** | 88 |

Both supply figures are exact maxima (a networkx maximum-independent-set check agreed with the greedy result). The real supply will be lower, because some families will fail oracle eligibility (a mutation the token oracle cannot observe) or provenance review.

**The default 8/24/16/40 protocol cannot be filled from Pygments under this grouping rule, even before rejections.** §6 then requires reporting the deficit and revising the protocol before execution. Silently filling the shortfall with related siblings is not allowed.

## Options for the protocol revision (reviewer decision)

1. **Add a second SWE-smith repository** with a deterministic text oracle (mistune, sqlglot, sqlparse or pypika; see `docs/benchmark-sources.md`). This keeps the 40-task final but needs a new `OracleSpec` contract, wider `TaskSpec` paths and intake for that repository.
2. **Freeze a smaller descriptive protocol on Pygments alone**, for example 8/16/12/30 (66 families). That leaves room for rejections, but the final comparison has less power than the already weak 40-task screen.
3. **Review the grouping rule.** If a reviewer decides that some direct imports (for example, of a shared token helper) are not related repairs, supply rises. This must be decided outcome-blind and recorded before partitioning.

## Effort receipts

The checkpoint returns `BLOCKED` until it has effort receipts for eight admitted, distinct families. The earlier offline validation did not record hours. Each receipt is one JSON record:

```json
{"family_id": "pygments/lexers/sql.py", "task_id": "…", "preparation_hours": 1.5,
 "review_hours": 0.5, "admitted": true, "rejection_reason": null, "reviewer_id": "…"}
```

With receipts and a frozen `--effort-limit-hours`, the decision is `PROCEED` only when the projected review count fits the remaining independent supply and the projected hours fit the limit. Otherwise it is `REVISE_PROTOCOL`, and a reviewer chooses between stopping and a smaller protocol.
