import hashlib
import json

import pytest

from recursive_run.campaign import Campaign, CampaignPlan, Proposal, RolloutResult
from recursive_run.ledger import Blocked, Ledger
from recursive_run.learning import REUSE_FIELDS, PackingPolicy

TASKS = [f"task{i:02d}" for i in range(80)]
POLICY = PackingPolicy(200_000, 1200, 50_000, 300_000, "v1", "test-bytes")


def count(data):
    return len(data) // 4 + 1


def plan(**extra):
    return CampaignPlan(
        campaign="c1",
        discovery=tuple(TASKS[:24]),
        selection=tuple(TASKS[24:40]),
        final=tuple(TASKS[40:]),
        families={t: "fam-" + t for t in TASKS},
        packing=POLICY,
        reuse_context={k: k for k in REUSE_FIELDS - {"campaign"}},
        **extra,
    )


class MockRollouts:
    """Deterministic: success depends on the task, replicate and the skill's 'tips'."""

    def __init__(self, fail_keys=()):
        self.calls = []
        self.fail_keys = set(fail_keys)

    def run(self, key, task_id, skill, arm, phase, replicate):
        self.calls.append((phase, arm, task_id, replicate))
        if key in self.fail_keys:
            raise RuntimeError("worker unavailable")
        tips = skill.count(b"tip")
        h = int(hashlib.sha256(f"{task_id}:{replicate}".encode()).hexdigest(), 16) % 100
        success = h < 20 + 15 * tips
        return RolloutResult(
            key,
            task_id,
            arm,
            phase,
            hashlib.sha256(skill).hexdigest(),
            replicate,
            success,
            1000,
            "SUCCEEDED" if success else "FAILED",
            evidence=f"task {task_id}: {'passed' if success else 'failed hidden checks'}",
        )


class MockProposer:
    """R adds a tip each round; B repeats R's round-1 text in round 2 (cross-arm alias)
    and returns an empty skill in round 3 (invalid; the slot is consumed)."""

    def __init__(self):
        self.calls = []

    def propose(self, campaign, round_number, arm, packet):
        self.calls.append((round_number, arm, json.loads(packet.history)))
        parent = packet.parent.decode()
        if arm == "R":
            return Proposal(
                (parent + f"\ntip r{round_number}").encode(),
                f"R{round_number}",
                500,
                f"op-R{round_number}",
            )
        if round_number == 1:
            return Proposal(b"Base skill.\ntip b", "B1", 500, "op-B1")
        if round_number == 2:
            return Proposal(b"Base skill.\ntip r1", "B2 copies R1", 500, "op-B2")
        return Proposal(b"", "B3 empty", 500, "op-B3")


def make(tmp_path, rollouts=None, proposer=None, **ledger_caps):
    ledger = Ledger(tmp_path / "db", cap=280_000_000, **ledger_caps)
    campaign = Campaign(
        ledger,
        plan(),
        rollouts or MockRollouts(),
        proposer or MockProposer(),
        count,
        b"Base skill.",
    )
    return ledger, campaign


def test_full_schedule_aliasing_history_and_sealed_final(tmp_path):
    rollouts, proposer = MockRollouts(), MockProposer()
    ledger, campaign = make(tmp_path, rollouts, proposer)
    try:
        campaign.baseline()
        for r in (1, 2, 3):
            campaign.round(r)
        with pytest.raises(ValueError, match="sealed"):
            campaign.report()
        assert campaign.progress()["final_done"] is False
        report = campaign.run()
        phases = {}
        for phase, *_ in rollouts.calls:
            phases[phase] = phases.get(phase, 0) + 1
        # S: 24 discovery + 48 selection; R discovery: 16; candidates x 48; final: 360.
        assert phases["discovery"] == 24 + 16
        assert phases["final"] == 360
        # R1, R2, R3 and B1 are new (4 x 48); B2 aliases R1 and B3 is rejected.
        assert phases["selection"] == 48 + 4 * 48
        assert len(proposer.calls) == 6
        # R history never contains B proposals and grows one record per round.
        r_histories = [h for (_, arm, h) in proposer.calls if arm == "R"]
        assert [len(h) for h in r_histories] == [0, 1, 2]
        assert all(rec.get("hypothesis", "").startswith("R") for h in r_histories for rec in h)
        statuses = dict(
            ((row["round"], row["arm"]), row["status"])
            for row in ledger.db.execute("SELECT round, arm, status FROM proposals")
        )
        assert statuses[(2, "B")] == "ALIASED" and statuses[(3, "B")] == "REJECTED"
        assert report["replicates_per_cell"] == 3 and report["assigned_denominator"] == 40
        assert report["artifact_hashes"]["R"] != report["artifact_hashes"]["S"]
        assert report["mean_success_rate"]["R"] > report["mean_success_rate"]["S"]
    finally:
        ledger.close()


def test_resume_is_a_no_op_and_failed_cells_count_as_failures(tmp_path):
    rollouts = MockRollouts()
    ledger, campaign = make(tmp_path, rollouts)
    try:
        first = campaign.run()
        calls = len(rollouts.calls)
        again = Campaign(ledger, plan(), rollouts, MockProposer(), count, b"Base skill.").run()
        assert len(rollouts.calls) == calls
        assert again["mean_success_rate"] == first["mean_success_rate"]
    finally:
        ledger.close()
    # A rollout that raises is recorded once as an interrupted failure, never retried.
    flaky = MockRollouts()
    ledger2, probe = make(tmp_path / "b", flaky)
    try:
        probe.baseline()
        key = ledger2.db.execute("SELECT key FROM campaign_rollouts LIMIT 1").fetchone()[0]
    finally:
        ledger2.close()
    failing = MockRollouts(fail_keys={key})
    ledger3, campaign3 = make(tmp_path / "c", failing)
    try:
        campaign3.baseline()
        row = ledger3.db.execute(
            "SELECT result FROM campaign_rollouts WHERE key=?", (key,)
        ).fetchone()
        result = json.loads(row[0])
        assert result["success"] is False and result["state"].startswith("INTERRUPTED")
        campaign3.baseline()
        assert sum(1 for c in failing.calls if c[0] == "discovery") == 24
    finally:
        ledger3.close()


def test_failed_proposal_consumes_slot_without_retry(tmp_path):
    class Exploding(MockProposer):
        def propose(self, campaign, round_number, arm, packet):
            if (round_number, arm) == (1, "R"):
                raise TimeoutError("reply lost")
            return super().propose(campaign, round_number, arm, packet)

    proposer = Exploding()
    ledger, campaign = make(tmp_path, proposer=proposer)
    try:
        campaign.baseline()
        campaign.round(1)
        campaign.round(1)  # resume: no second call for a consumed slot
        assert [c[:2] for c in proposer.calls] == [(1, "B")]
        status = ledger.db.execute(
            "SELECT status FROM proposals WHERE round=1 AND arm='R'"
        ).fetchone()[0]
        assert status == "REJECTED"
        # The uncertain proposal keeps its reservation until reconciled.
        reserved = ledger.db.execute(
            "SELECT actual FROM obligations WHERE id='proposal:c1:1:R'"
        ).fetchone()[0]
        assert reserved is None
    finally:
        ledger.close()


def test_proposal_cap_is_enforced_by_the_ledger(tmp_path):
    ledger = Ledger(
        tmp_path / "db",
        cap=280_000_000,
        category_caps={"coding_model": 1, "proposal": 1_000_000, "infrastructure": 1},
    )
    try:
        campaign = Campaign(ledger, plan(), MockRollouts(), MockProposer(), count, b"Base skill.")
        campaign.baseline()
        with pytest.raises(Blocked, match="category cap"):
            campaign.round(1)
    finally:
        ledger.close()


def test_plan_rejects_overlap_and_wrong_sizes():
    with pytest.raises(ValueError):
        CampaignPlan(
            campaign="c",
            discovery=tuple(TASKS[:24]),
            selection=tuple(TASKS[:16]),
            final=tuple(TASKS[40:]),
            families={},
            packing=POLICY,
            reuse_context={k: k for k in REUSE_FIELDS - {"campaign"}},
        )
