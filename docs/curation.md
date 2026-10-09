# Task curation for RL1

Goal: 88 independent-family tasks (8 smoke + 24 discovery + 16 selection + 40 final). The 8 smoke families are excluded from the other partitions. Hidden inputs, frozen expected outputs, traces and dataset rows stay in ignored `.runtime/curation/`. This file records the method and the counts only.

## Method

1. **Candidates.** Take the outcome-blind independent families from the supply analysis (`docs/family-supply.md`). For each family, list single-file mutations, preferring simple procedural ones (`func_pm_*`), then `func_basic`, `lm_rewrite` and mirrored-PR (`pr_*`) origins.
2. **Observability screen (development, not isolated).** Draft newly authored inputs and run reference and broken code in a plain local subprocess. Keep only inputs where the broken output differs and the reference output is identical across two runs. A mutation whose effect cannot be observed through the frozen grader is rejected, and when the family has one, another mutation from the same family is tried.
3. **Intake (isolated).** `scripts/run_intake.py <repo>` runs `smoke.prepare` in an E2B preparation sandbox for every draft:
   - reconstruct the broken code from the bug patch;
   - verify that the reverse patch restores the reference byte for byte;
   - run the reference twice and the broken code once on both hidden inputs;
   - accept the task only if the reference is deterministic and the broken code fails.

   The snapshot is staged once per sandbox. Spend goes to a separate curation ledger capped at $1.
4. **Leak check.** No hidden input may appear in the task's public problem statement.
5. **Review.** All drafts were written by Claude. Human review of inputs, grouping and problem statements, with effort hours, is still required (`EffortReceipt`) before partitions are frozen.

Grader additions made during curation: a `sqlglot.qualify` pipeline and an `isolate_tables` option, both needed so that qualify-stage mutations are observable.

## Status (9 October 2026)

| Source | Families tried | Drafted with separating inputs | Passed E2B intake |
|---|---|---|---|
| sqlglot (smoke) | 8 | 8 | 8 |
| sqlglot (curation) | 20 | 15 | 15 |
| Pygments | 82 | 67 | in progress |

sqlglot families not drafted: the two executor modules and `serde` (would need a table-execution or serialization pipeline), `lineage` (the mutation affects HTML output containing object ids, so it cannot be compared deterministically), and `diff` (the rewrite appears behavior-preserving). In-family replacements: `unnest_subqueries` (`xjjaistr` → `nricthbi`) and `qualify_columns` (`vbedhpfx` → `8f6ggzj1`).

Pygments rejections at the screen:

- Mutations that change only `analyse_text` (language guessing): token output cannot observe them.
- `RawTokenLexer`: it overrides `get_tokens_unprocessed` itself, so removing its base class is invisible.
- Kconfig indentation (`251t2x4p`): no separating input found; replaced in-family by `cmf7v0up`.
- Urbi blob callback: no separating input found.
- In-family replacements: `hdl` (`pr_2622`), `prolog` (`pr_2619`), `make` (`pr_2354`), `mojo` (`323ohrxl`), `configs` (`cmf7v0up`).

Intake rejections are recorded in the receipts. Example: the GAP console lexer emits out-of-order token offsets in the reference, which the frozen lexer contract refuses.
