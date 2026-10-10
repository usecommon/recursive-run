# RL1 analysis plan (provisional)

This plan is frozen before any learning outcome exists. It becomes binding once the task-pool review is recorded (`docs/review-checklist.md`). Code: `analysis.replicated_screening_report`, `learning.coding_schedule(3, 3)`.

## Design deltas from v1.1

| Item | v1.1 | RL1 decision (2026-10-09) | Reason |
|---|---|---|---|
| Final replicates per task/arm | 1 | 3 | Smoke: 4 of 8 outcomes flipped between identical runs |
| Selection replicates per candidate/task | 1 | 3 | Rank skills on less noisy vectors |
| Format-error tolerance | 1 | 3 consecutive, with parser feedback (each is a counted step) | Smoke v1: 2 of 8 attempts ended on one malformed reply |
| Worker template | — | `rl-worker-py312-tools` (pinned base plus ripgrep) | Smoke v2: missing-tool steps |
| Tasks | Pygments only | sqlglot 24 (8 smoke) + Pygments 64 | Pygments supports at most 82 independent families |

Unchanged: 20 model steps, 10-minute rollouts, one 1,200-token skill file, six proposal calls, discovery evidence rules, and sealed final outcomes.

## Partitions

Defined in `configs/partition-v1.json`, an outcome-blind draw:

- **Smoke:** the 8 smoke families (sqlglot), used only for feasibility.
- **Other partitions:** drawn at random, stratified by repository, with the seed derived from the pool hash.
  - Discovery: 24 tasks (5 sqlglot, 19 Pygments).
  - Selection: 16 tasks (3, 13).
  - Final: 40 tasks (8, 32).
- No family appears in two partitions.

## Schedule (maximum coding rollouts)

| Phase | Rollouts |
|---|---|
| Smoke | 8 (done) |
| S on discovery | 24 |
| S on selection, 3 replicates | 48 |
| R discovery, rounds 2–3 | 16 |
| Six candidates × 16 selection × 3 | 288 |
| Final: 3 arms × 40 × 3 | 360 |
| **Total after smoke** | **736** |

## Primary analysis

- **Unit of analysis.** The task. For each task and arm, the success rate is the mean of its 3 verified replicate outcomes. Every assigned task stays in the denominator. A replicate that times out, is invalid or is missing counts as a failure.
- **Primary contrast.** R − S mean success-rate difference. The secondary contrast is R − B.
- **Interval.** Paired task percentile bootstrap: 10,000 resamples, seed 2718, 95%. Each resample draws whole tasks with their complete S/B/R replicate vectors. The interval is reported only if the sampling receipt is justified; otherwise it is `UNESTIMABLE`.
- **Task-level wins and losses.** A win or loss is a majority flip: one arm succeeds in more than half its replicates and the other in at most half. Replicates are never counted as extra tasks.
- **Decision.** The v1.1 thresholds, applied to replicate means. `PROMISING_FOR_CONFIRMATION` requires all of:
  - R − S ≥ 0.10;
  - R − B ≥ 0.05;
  - at most 2 majority losses against S;
  - R/S cost per verified success ≤ 1.25 (over all replicates);
  - R's artifact differs from both S and B.

  An integrity failure gives `STOP`. Anything incomplete or unreconciled gives `INCONCLUSIVE`.
- **Also reported.** Within-task replicate agreement (execution reliability). Method reliability across learning campaigns stays unestimated in RL1.

## Selection ranking

Each candidate's 16-task selection vector holds 3 replicates per task. Ranking order:

1. total verified successes over all 48 rollouts, descending;
2. selection cost, ascending;
3. skill length, ascending;
4. canonical version, earlier last.

The learner sees only aggregate counts and cost (v1.1 §9).

## Budget

Expected spend at smoke rates is about $12. The design's worst-case reservation conflicts with the $50 cap; see the `budget` section of `configs/rl1-decisions.json`. Learning stays unauthorized until that decision is made.
