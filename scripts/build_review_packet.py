"""Private human-review packet for the 88-task pool, plus an effort-receipt template.

Writes .runtime/review/packet.md (hidden inputs, expected outputs and golden patches:
never commit or share it) and .runtime/review/effort-receipts.json (fill in, then feed
to scripts/family_supply_checkpoint.py --receipts). Public checklist: docs/review-checklist.md.
"""

import glob
import json
from pathlib import Path

import pyarrow.parquet as pq

OUT = Path(".runtime/review")
SMOKE_AUTHORED = {
    "func_pm_ctrl_invert_if__4ld5094d",
    "func_pm_ctrl_invert_if__tdrra8m5",
}


def main():
    pool = json.loads(Path("configs/task-pool-v1.json").read_text())
    partition = json.loads(Path("configs/partition-v1.json").read_text())["spec"]
    where = {
        t: name for name in ("smoke", "discovery", "selection", "final") for t in partition[name]
    }
    inputs = {
        **json.loads(Path(".runtime/smoke/inputs.json").read_text()),
        **json.loads(Path(".runtime/curation/inputs-sqlglot.json").read_text()),
        **json.loads(Path(".runtime/curation/inputs-pygments.json").read_text()),
    }
    frozen = {
        **json.loads(Path(".runtime/smoke/frozen.json").read_text()),
        **json.loads(Path(".runtime/curation/frozen-sqlglot.json").read_text()),
        **json.loads(Path(".runtime/curation/frozen-pygments.json").read_text()),
    }
    rows = {}
    for path in sorted(glob.glob(".runtime/benchmarks/swe-smith-py/raw/*.parquet")):
        table = pq.read_table(
            path,
            columns=["instance_id", "repo", "patch", "problem_statement"],
            filters=[
                (
                    "repo",
                    "in",
                    ["swesmith/tobymao__sqlglot.036601ba", "swesmith/pygments__pygments.27649ebb"],
                )
            ],
        )
        for row in table.to_pylist():
            rows[row["instance_id"]] = row
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Task pool review packet (PRIVATE: hidden inputs and golden patches)",
        "",
        "Checklist: docs/review-checklist.md. Record time spent per task in "
        "effort-receipts.json next to this file.",
        "",
    ]
    receipts = []
    for n, task in enumerate(
        sorted(pool["tasks"], key=lambda t: (where[t["task_id"]], t["task_id"])), 1
    ):
        tid, short = task["task_id"], task["task_id"].rsplit(".", 1)[-1]
        row, draft = rows[tid], inputs[short]
        statement = row["problem_statement"] or ""
        if short in SMOKE_AUTHORED:
            statement = "(Authored replacement statement used in smoke; see scripts/run_smoke.py.)"
        grader = (
            f"lexer {draft['module']}.{draft['class_name']} options={draft.get('options', {})}"
            if "class_name" in draft
            else f"{draft['pipeline']} options={json.dumps(draft['options'])[:300]}"
        )
        expected = frozen.get(tid, {}).get("expected", [])
        lines += [
            f"## {n}. `{short}` ({where[tid]})",
            "",
            f"- Repository/family: `{task['family']}`; origin: `{short.split('__')[0]}`",
            f"- Grader: {grader}",
            f"- Broken outcomes at intake: {task['broken_outcomes']}",
            "",
            "**Problem statement shown to the agent**",
            "",
            "```text",
            statement.strip()[:2500],
            "```",
            "",
            "**Bug patch (reverse it to get the fix)**",
            "",
            "```diff",
            row["patch"].strip()[:3000],
            "```",
            "",
            "**Hidden inputs**",
            "",
            "```text",
            "DISTINGUISHING:",
            draft["distinguishing"],
            "REGRESSION:",
            draft["regression"],
            "```",
            "",
            "**Expected reference output (truncated)**",
            "",
            "```json",
            *(e[:600] for e in expected),
            "```",
            "",
            "- [ ] statement accurate and does not reveal the fix",
            "- [ ] distinguishing input exercises the bug; regression input checks nearby behavior",
            "- [ ] inputs are new (not copied from the repository's tests or examples)",
            "- [ ] expected output is plausible correct behavior",
            "- [ ] origin and license recorded; no relation to another pool family noticed",
            "",
        ]
        receipts.append(
            {
                "family_id": task["family"],
                "task_id": tid,
                "preparation_hours": 0.0,
                "review_hours": None,
                "admitted": None,
                "rejection_reason": None,
                "reviewer_id": "",
            }
        )
    (OUT / "packet.md").write_text("\n".join(lines) + "\n")
    template = OUT / "effort-receipts.json"
    if not template.exists():  # never overwrite a reviewer's entries
        template.write_text(json.dumps(receipts, indent=1) + "\n")
    print(f"{len(receipts)} tasks -> {OUT / 'packet.md'}; template {template}")


if __name__ == "__main__":
    main()
