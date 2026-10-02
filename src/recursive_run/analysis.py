"""Frozen paired screening analysis; no population inference without sampling receipts."""

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class FinalOutcome:
    task: str
    family: str
    s: bool
    b: bool
    r: bool
    s_cost: int
    b_cost: int
    r_cost: int
    r_regression_check_failed: bool = False

    def __post_init__(self):
        if any(type(x) is not bool for x in [self.s, self.b, self.r]):
            raise ValueError("assigned outcomes must be verified booleans")
        if any(type(x) is not int or x < 0 for x in [self.s_cost, self.b_cost, self.r_cost]):
            raise ValueError("assigned cash must be nonnegative micro-USD")


def paired(rows: list[FinalOutcome], left: str, right: str):
    wins = sum(getattr(row, left) and not getattr(row, right) for row in rows)
    losses = sum(getattr(row, right) and not getattr(row, left) for row in rows)
    unchanged = len(rows) - wins - losses
    discordant = wins + losses
    exact_p = (
        min(
            1.0,
            2 * sum(math.comb(discordant, i) for i in range(min(wins, losses) + 1)) / 2**discordant,
        )
        if discordant
        else None
    )
    return {
        "wins": wins,
        "losses": losses,
        "unchanged": unchanged,
        "difference": (wins - losses) / len(rows) if rows else None,
        "supplemental_exact_mcnemar_p": exact_p,
    }


def percentile(values, probability):
    position = (len(values) - 1) * probability
    lower = int(position)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (
        position - lower
    )


def paired_intervals(rows, sampling_justified: bool):
    if not sampling_justified or len({row.family for row in rows}) != len(rows):
        return {"status": "UNESTIMABLE", "reason": "sampling/independence contract unestablished"}
    random_source = random.Random(2718)
    rs, rb = [], []
    n = len(rows)
    for _ in range(10000):
        sample = [rows[random_source.randrange(n)] for _ in range(n)]
        rs.append(sum(int(row.r) - int(row.s) for row in sample) / n)
        rb.append(sum(int(row.r) - int(row.b) for row in sample) / n)
    result = {"status": "ESTIMATED", "resamples": 10000, "seed": 2718, "confidence": 0.95}
    for label, values in [("R-S", rs), ("R-B", rb)]:
        values.sort()
        low, high = percentile(values, 0.025), percentile(values, 0.975)
        result[label] = {"interval": [low, high], "degenerate_uninformative": low == high}
    return result


def screening_report(
    rows: list[FinalOutcome],
    assigned_tasks: list[str],
    hashes: dict[str, str],
    sampling_justified=False,
    integrity_valid=False,
    reconciled=False,
    invalidity_flags=(),
    research_cash=None,
    human_hours=None,
):
    if len(set(assigned_tasks)) != len(assigned_tasks):
        raise ValueError("duplicate assigned task")
    if len({row.task for row in rows}) != len(rows) or any(
        row.task not in assigned_tasks for row in rows
    ):
        raise ValueError("unexpected or duplicate outcome row")
    if set(hashes) != {"S", "B", "R"}:
        raise ValueError("freeze all three artifact hashes")
    complete = len(rows) == len(assigned_tasks) and bool(assigned_tasks)
    rs = paired(rows, "r", "s")
    rb = paired(rows, "r", "b")
    counts = {arm.upper(): sum(getattr(row, arm) for row in rows) for arm in ("s", "b", "r")}
    costs = {
        arm.upper(): sum(getattr(row, arm + "_cost") for row in rows) for arm in ("s", "b", "r")
    }
    per_success = {arm: costs[arm] / counts[arm] if counts[arm] else None for arm in counts}
    ratio = (
        per_success["R"] / per_success["S"]
        if per_success["R"] is not None and per_success["S"] is not None and per_success["S"] > 0
        else None
    )
    identical = hashes["R"] in {hashes["S"], hashes["B"]}
    flags = list(invalidity_flags)
    if not integrity_valid:
        flags.append("integrity_unverified")
    if not integrity_valid or invalidity_flags:
        decision = "STOP"
    elif not complete or not reconciled or ratio is None:
        decision = "INCONCLUSIVE"
    elif (
        rs["difference"] >= 0.10
        and rb["difference"] >= 0.05
        and rs["losses"] <= 2
        and ratio <= 1.25
        and not identical
    ):
        decision = "PROMISING_FOR_CONFIRMATION"
    else:
        decision = "INCONCLUSIVE"
    return {
        "schema_version": 1,
        "state": decision,
        "complete": complete,
        "reconciled": reconciled,
        "assigned_denominator": len(assigned_tasks),
        "completed_task_vectors": len(rows),
        "assigned_tasks": assigned_tasks,
        "artifact_hashes": hashes,
        "verified_successes": counts,
        "R-S": rs,
        "R-B": rb,
        "intervals": paired_intervals(rows, sampling_justified)
        if complete
        else {"status": "UNESTIMABLE"},
        "lost_S_successes": rs["losses"],
        "loss_fraction_all_tasks": rs["losses"] / len(assigned_tasks) if assigned_tasks else None,
        "severe_behavioral_regressions": sum(
            row.s and not row.r and row.r_regression_check_failed for row in rows
        ),
        "final_execution_cash_micro_usd": costs,
        "cost_per_success_micro_usd": per_success,
        "R_to_S_cost_per_success": ratio,
        "identical_artifact_attribution_forbidden": identical,
        "invalidity_flags": flags,
        "total_research_cash_micro_usd": research_cash,
        "human_hours": human_hours,
        "execution_reliability": "UNESTIMATED",
        "method_reliability": "UNESTIMATED",
        "practical_disposition": None,
    }
