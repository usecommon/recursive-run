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
