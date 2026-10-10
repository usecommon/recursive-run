# Task pool review checklist

The design requires human review of the task pool before partitions are frozen (v1.1 §6–7). All 88 accepted tasks were drafted by Claude: hidden inputs, grouping and two authored problem statements. Intake has already verified the mechanics in isolated sandboxes (reverse-patch chain, deterministic reference, broken code fails). A reviewer judges what the machine cannot.

## Materials

Build the private packet (hidden inputs, golden patches; never commit or share it):

```sh
uv run python scripts/build_review_packet.py
# -> .runtime/review/packet.md and .runtime/review/effort-receipts.json
```

## Per task (5 checks)

1. **Problem statement.** Describes the real bug in the right direction. Doesn't give away the fix or the hidden inputs. Two smoke statements were replaced because the generated ones were wrong; check that the others are right.
2. **Hidden inputs.**
   - The distinguishing input exercises the mutated behavior.
   - The regression input checks nearby behavior. It may also fail on the broken code.
   - Both are newly written, not copied from the repository's tests or example files.
3. **Expected output.** The reference output is plausible correct behavior for the input. Reference code defines "correct", but an obviously absurd expectation means the input is a poor choice.
4. **Independence and origin.** No relationship noticed with another pool family, such as shared helpers or near-duplicate code. The origin type (procedural `func_pm_*`/`func_basic`, LM-rewritten `lm_rewrite`, mirrored PR `pr_*`) and the license (sqlglot MIT, Pygments BSD-2-Clause) are recorded.
5. **Time.** Record the time spent in `effort-receipts.json`:
   - `review_hours` and `reviewer_id`;
   - `admitted` true or false;
   - `rejection_reason` when false.

## After review

1. **Effort checkpoint.** At least 8 receipts are needed, and the design's checkpoint applies to the first eight reviewed families. Freeze an effort limit, then run:

   ```sh
   uv run python scripts/family_supply_checkpoint.py --repository sqlglot --source <sqlglot checkout> \
       --receipts .runtime/review/effort-receipts.json --effort-limit-hours <N>
   ```

2. **Replacements.** Rejected tasks are replaced from the same repository under the substitution policy in `configs/partition-v1.json`, before any outcome on that partition is visible.
3. **Mark the pool reviewed.** Set `human_reviewed: true` in a new `configs/task-pool-v2.json` and redraw or confirm the partition. The draw is reproducible from the pool hash.
