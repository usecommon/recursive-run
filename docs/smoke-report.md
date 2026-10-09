# RL0 live smoke report: sqlglot, baseline skill S

**Date:** 8 October 2026. **Scope:** feasibility smoke only (design v1.1 §6, §11, §16). This report is not evidence of learning. It measures whether the harness runs end to end under its caps, and where the baseline floor and ceiling sit.

## Setup

| Item | Value |
|---|---|
| Executor model | `gpt-4.1-mini-2025-04-14`, temperature 0, ≤ 2,048 output tokens per call |
| Agent | mini-swe-agent 2.4.6 through the controller gateway; 20 model steps, 10 min, 60 s per command |
| Worker | E2B template `rl-worker-py312` (`python:3.12.14-slim` by digest, 2 vCPU / 2 GiB), egress blocked, provider-enforced lifetime |
| Tasks | 8 sqlglot mutations from 8 independent families (single-file, procedurally generated) |
| Oracle | `CallContract` (sqlglot transpile), one distinguishing and one regression hidden input per task, exact comparison |
| Skill | Baseline S (`configs/smoke/skill-S.md`, 134 tokens) |
| Storage | SQLite ledger on the ephemeral container disk, under a recorded **smoke-only storage waiver** |

## Intake (all in E2B preparation sandboxes)

All 8 tasks were accepted:

- The bug patch reconstructed the broken code, and the reverse patch restored the reference byte for byte.
- The reference passed both hidden checks twice, identically.
- The broken code failed at least one check.

Intake cost $0.004. Two dataset problem statements (`4ld5094d`, `tdrra8m5`) contradicted the reference behavior or gave an example that does not reproduce. They were replaced by short authored statements; the replacement is recorded in the receipts.

## Attempts

| Task | Family | State | Agent exit | Hidden checks | Steps | Seconds | Cost |
|---|---|---|---|---|---|---|---|
| `func_pm_ctrl_invert_if__4ld5094d` | dialects/sqlite.py | SUCCEEDED | Submitted | PASS, PASS | 18 | 49.2 | $0.0160 |
| `func_pm_ctrl_invert_if__5liurb5h` | dialects/snowflake.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 56.2 | $0.0131 |
| `func_pm_ctrl_invert_if__2jq6jsa6` | time.py | FAILED | RepeatedFormatError | FAIL, FAIL | 4 | 25.0 | $0.0046 |
| `func_pm_ctrl_invert_if__tdrra8m5` | dialects/duckdb.py | FAILED | RepeatedFormatError | not scored | 1 | 5.5 | $0.0006 |
| `func_pm_ctrl_invert_if__r7jh47l4` | dialects/bigquery.py | FAILED | Submitted | FAIL, PASS | 16 | 45.6 | $0.0159 |
| `func_pm_ctrl_shuffle__59jw6514` | dialects/oracle.py | SUCCEEDED | Submitted | PASS, PASS | 14 | 40.5 | $0.0103 |
| `func_pm_ctrl_shuffle__1ryv0pj6` | dialects/redshift.py | FAILED | LimitsExceeded | FAIL, PASS | 20 | 51.2 | $0.0175 |
| `func_pm_ctrl_shuffle__cec6uywd` | dialects/spark.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 51.9 | $0.0201 |
| `func_pm_ctrl_invert_if__4ld5094d` (first try) | | INTERRUPTED | harness bug, see note | not recorded | 12 | | $0.0109 |

**Verified successes: 2 of 8 (25%).** "Submitted" is the agent's own claim; only the hidden checks decide success. One wrong submission (`r7jh47l4`) was caught by the distinguishing input.

## Findings

1. **The harness works end to end** through the managed proxy: gateway admission, durable leases, E2B coding sandbox, patch export by byte diff, a fresh E2B scoring sandbox per attempt, exact comparison and ledger settlement. 0 sandboxes were left running.
2. **The baseline is neither at the floor nor at the ceiling** (2/8), so a learned skill has room to show an effect in either direction. Eight tasks do not estimate the baseline precisely.
3. **The step limit binds; time and cash do not.** 3 of 8 attempts ended at 20 model steps, after about 50 s each. No attempt approached the 10-minute or $0.50 rollout ceilings: the most expensive cost $0.020.
4. **Format errors end attempts.** The frozen protocol allows one malformed reply (`max_consecutive_format_errors=1`). 2 of 8 attempts (25%) ended that way, one on its very first reply. The rejected replies were not recorded, so the cause is unknown. Traces now keep the parser's reason and the rejected reply. Whether to keep this protocol rule is a decision to make before learning.
5. **Intake must review problem statements.** 2 of 8 generated statements were wrong.
6. **Harness bug, fixed.** The first full run omitted the oracle result on a passing attempt, which the ledger correctly refused. That attempt is recorded as INTERRUPTED (its trace and patch were lost), and the task was re-attempted as a new occurrence. Both are shown above.

## Cost

| Item | Spend |
|---|---|
| OpenAI coding calls (9 attempts incl. the interrupted one) | $0.105 |
| E2B: intake, attempts, scoring | $0.018 |
| E2B: live probes (5 short sandboxes) | ≤ $0.012 |
| **Total** | **≈ $0.13 of the $25 smoke authorization** |

Projected from these rates, 280 rollouts would cost about $5–6 in model calls (budget $140) and a few dollars of E2B time (budget $20). That leaves the design's ceilings very conservative for this model.

## Gate status

| Gate | Status |
|---|---|
| Live feasibility: prices bound, expiry enforced, smoke within the stop budget | **Met** |
| Durable storage | **Open**: smoke ran under a recorded waiver; learning and final runs stay blocked |
| Operator authentication | Open (the operator UI is deferred until after smoke) |
| Family supply and effort checkpoint | Open: no human effort receipts yet |

Private evidence (hidden inputs, traces, patches, ledger) is in ignored `.runtime/smoke/`. Reproduce with `scripts/run_smoke.py intake|attempts` and `scripts/summarize_smoke.py`.


## Protocol v2 rerun (9 October 2026)

v2 was frozen and committed before it ran. The only change: up to 3 consecutive malformed replies are tolerated, and the parser's reason is fed back. Each malformed reply still counts as a model step. Same 8 tasks, same intake and hidden inputs, skill S.

| Task | Family | State | Agent exit | Hidden checks | Steps | Seconds | Cost |
|---|---|---|---|---|---|---|---|
| `func_pm_ctrl_invert_if__4ld5094d` | dialects/sqlite.py | FAILED | LimitsExceeded | not scored | 20 | 50.9 | $0.0186 |
| `func_pm_ctrl_invert_if__5liurb5h` | dialects/snowflake.py | SUCCEEDED | Submitted | PASS, PASS | 7 | 20.4 | $0.0036 |
| `func_pm_ctrl_invert_if__2jq6jsa6` | time.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 45.3 | $0.0153 |
| `func_pm_ctrl_invert_if__tdrra8m5` | dialects/duckdb.py | FAILED | LimitsExceeded | MALFORMED, PASS | 20 | 39.6 | $0.0131 |
| `func_pm_ctrl_invert_if__r7jh47l4` | dialects/bigquery.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 45.8 | $0.0308 |
| `func_pm_ctrl_shuffle__59jw6514` | dialects/oracle.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 42.5 | $0.0204 |
| `func_pm_ctrl_shuffle__1ryv0pj6` | dialects/redshift.py | SUCCEEDED | Submitted | PASS, PASS | 16 | 36.7 | $0.0161 |
| `func_pm_ctrl_shuffle__cec6uywd` | dialects/spark.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 41.9 | $0.0145 |

**Verified successes: 2 of 8 again, but a different two.**

| Task | v1 | v2 | Flipped |
|---|---|---|---|
| `func_pm_ctrl_invert_if__4ld5094d` | SUCCEEDED | FAILED | yes |
| `func_pm_ctrl_invert_if__5liurb5h` | FAILED | SUCCEEDED | yes |
| `func_pm_ctrl_invert_if__2jq6jsa6` | FAILED | FAILED |  |
| `func_pm_ctrl_invert_if__tdrra8m5` | FAILED | FAILED |  |
| `func_pm_ctrl_invert_if__r7jh47l4` | FAILED | FAILED |  |
| `func_pm_ctrl_shuffle__59jw6514` | SUCCEEDED | FAILED | yes |
| `func_pm_ctrl_shuffle__1ryv0pj6` | FAILED | SUCCEEDED | yes |
| `func_pm_ctrl_shuffle__cec6uywd` | FAILED | FAILED |  |

Findings:

1. **The format rule is fixed.** Both malformed replies (actions sent as bare strings instead of `{"command": ...}`) were followed by a valid reply after feedback. No v2 attempt ended on a format error.
2. **Run-to-run variance is large.** 4 of 8 task outcomes flipped between two runs at temperature 0, while the total stayed at 2/8. A single run per task cannot attribute a 1–2 task difference between arms to learning. This bears directly on the 40-task, one-repeat final comparison.
3. **The step limit is now the main failure mode.** 6 of 8 v2 attempts ended at 20 steps, after 40–51 s each. Several steps went to `rg`, which the template did not provide (`jq` was present through E2B's build layer, but was often piped from a failing `rg`).
4. **Cost:** v2 used $0.133 for 8 attempts. Smoke total so far is about $0.26 of the $25 authorization.

Recommendations before learning (each needs a decision, frozen before learning data exists):

- **Tools:** either add `ripgrep` to the pinned template or state the available tools in the system prompt. Both are environment or prompt changes and require a new template build or protocol version.
- **Replicates:** use at least 3 replicates per final task (or more tasks), as allowed by the design's repeated-campaign guidance. At about $0.02 per rollout, 3 × 120 final rollouts cost about $7.


## Protocol v3 rerun (9 October 2026)

v3 was frozen and committed before it ran. The only change from v2 is the worker template, `rl-worker-py312-tools`: same pinned base, plus `ripgrep`. A live probe on the new template passed. Both templates already contained `git`, `curl` and `jq` from E2B's build layer.

| Task | Family | State | Agent exit | Hidden checks | Steps | Seconds | Cost |
|---|---|---|---|---|---|---|---|
| `func_pm_ctrl_invert_if__4ld5094d` | dialects/sqlite.py | FAILED | Submitted | FAIL, PASS | 6 | 25.7 | $0.0043 |
| `func_pm_ctrl_invert_if__5liurb5h` | dialects/snowflake.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 49.7 | $0.0194 |
| `func_pm_ctrl_invert_if__2jq6jsa6` | time.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 63.0 | $0.0189 |
| `func_pm_ctrl_invert_if__tdrra8m5` | dialects/duckdb.py | FAILED | Submitted | FAIL, PASS | 20 | 41.5 | $0.0127 |
| `func_pm_ctrl_invert_if__r7jh47l4` | dialects/bigquery.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 42.1 | $0.0182 |
| `func_pm_ctrl_shuffle__59jw6514` | dialects/oracle.py | FAILED | LimitsExceeded | FAIL, PASS | 20 | 44.1 | $0.0193 |
| `func_pm_ctrl_shuffle__1ryv0pj6` | dialects/redshift.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 65.2 | $0.0152 |
| `func_pm_ctrl_shuffle__cec6uywd` | dialects/spark.py | FAILED | LimitsExceeded | FAIL, FAIL | 20 | 72.9 | $0.0292 |

**Verified successes: 0 of 8.**

| Measure | v1 | v2 | v3 |
|---|---|---|---|
| Verified successes | 2/8 | 2/8 | 0/8 |
| Missing-tool observations (`command not found`) | 17 | 13 | **1** |
| Attempts at the 20-step limit | 3 | 6 | 7 |
| Edit actions / run actions | 34 / 25 | 43 / 23 | 61 / 34 |
| Spend | $0.12 | $0.13 | $0.14 |

Per-task outcomes across the three runs (S = succeeded): v1 succeeded on `4ld5094d`, `59jw6514`; v2 on `5liurb5h`, `1ryv0pj6`; v3 on none. No task succeeded twice.

Findings:

1. **ripgrep removed the wasted steps** (13 to 1 missing-tool observations), and the agent spent more of its steps editing and testing.
2. **Outcomes did not improve, but 0/8 is within noise.** With a per-task success rate near 25%, 0 of 8 happens about 10% of the time by chance. Pooled over the three runs, the baseline is 4 of 24 attempts (17%), and no task succeeded twice. The difference between protocols cannot be attributed to the template.
3. **The baseline is close to the floor.** At about 17%, about 7 of 40 final tasks would succeed for S. A learned skill would need several additional wins to stand out from this run-to-run variance in a single replicate.
4. **The 20-step limit binds in almost every attempt.** The agent now runs out of steps while editing and testing, not while searching.

Total smoke spend after three protocols: about $0.40 of the $25 authorization.

Revised recommendations:

- Keep the v3 template for learning. It removes an environment confound, and the outcome change is not distinguishable from noise.
- Replicates are essential: at least 3 per final task. Also consider 3 replicates in selection (tryout), so that skill ranking is not driven by single noisy draws.
- Review whether 20 steps is the intended difficulty for this model. The design fixes it at 20; changing it is a design revision, decided before learning.
