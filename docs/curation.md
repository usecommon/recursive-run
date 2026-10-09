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

## Status (9 October 2026): 88 tasks accepted

| Source | Families tried | Accepted after E2B intake |
|---|---|---|
| sqlglot (smoke) | 8 | 8 |
| sqlglot (curation) | 20 | 16 |
| Pygments | 82 | 64 |
| **Total** | | **88 tasks from 88 distinct families** |

The pool manifest is `configs/task-pool-v1.json`. It holds hashes only (contracts, expected outputs, bug and reverse patches) and is marked `human_reviewed: false`. No hidden input appears in its task's public problem statement. Isolated intake for curation cost $0.043 in total.

**Intake fix during Pygments curation.** The broken lexer crashed in 34 tasks. The worker exited non-zero, which is recorded as `WORKER_ERROR`, and intake correctly refused to count that as the broken code failing. The lexer path now reports exceptions from code under test as inert data, which fails the check as `MALFORMED`; `WORKER_ERROR` is reserved for real worker failures. All 34 were accepted on re-run.

**Rejections:**

- **Reference token offsets out of order (8):** the GAP, Julia and MATLAB console lexers, Postgres console, Fortran fixed-form, ArturoLang annotated strings, and Postgres embedded-language bodies. The frozen lexer contract requires nondecreasing offsets. This is a property of the reference code, so these mutations are ineligible. Alternatives in the same family were accepted for `arturo`, `fortran` and `sql`.
- **Only `analyse_text` changed (12):** language guessing, which token output cannot observe.
- **Unobservable through the graders (5):** `RawTokenLexer` base class; sqlglot `serde` (sqlglot has no expression classes outside its main module); `lineage` (output contains object ids); `diff` (the rewrite appears behavior-preserving); Kconfig and Urbi variants for which no separating input was found.
- **In-family replacements:** `unnest_subqueries`, `qualify_columns`, `hdl`, `prolog`, `make`, `mojo`, `configs`, `arturo`, `fortran`, `sql`.

Grader additions: `sqlglot.qualify`, `sqlglot.execute` (tables as a JSON-string option) and the `isolate_tables` option.

**Still required before partitions are frozen:**

- human review of all 88 drafts (inputs, grouping, problem statements), with effort receipts;
- the family-supply checkpoint;
- an outcome-blind draw into 24 discovery, 16 selection and 40 final tasks, with the 8 smoke families kept apart.
