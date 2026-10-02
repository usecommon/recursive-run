"""Exercise the exact 280-rollout maximum schedule with synthetic adapters only.

No provider/network calls; no candidate code execution. All outputs are labeled mock.
This verifies orchestration mechanics and cannot satisfy scientific or live gates.
"""

import argparse
import json
import random
import time
from pathlib import Path

from recursive_run.analysis import FinalOutcome, screening_report
from recursive_run.artifacts import ArtifactStore, digest
from recursive_run.contracts import Manifest, OracleResult, State
from recursive_run.gateway import DispatchCapabilities, Gateway, ModelResult
from recursive_run.learning import (
    Candidate,
    Evidence,
    LearningRegistry,
    PackingPolicy,
    REUSE_FIELDS,
    SelectionVector,
    canonical_skill,
    pack,
    reuse_key,
)
from recursive_run.ledger import Ledger
from recursive_run.projections import Projection


class MockProvider:
    capabilities = DispatchCapabilities(False, False, True, "synthetic-no-network-v1")

    def generate(self, admitted, request):
        return ModelResult(
            "mock:" + admitted.operation_key,
            b"mock repair",
            1000,
            {"input": len(request), "output": 11},
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.output / "ledger.sqlite").exists():
        raise SystemExit("Use a fresh output directory: a new campaign is a new occurrence.")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = Ledger(args.output / "ledger.sqlite", final_protected=60_000_000)
    artifacts = ArtifactStore(args.output / "artifacts")
    manifest = Manifest(experiment_id="mock-campaign", stage="RL1", mode="mock")
    gateway = Gateway(ledger, manifest, MockProvider(), 1000)
    registry = LearningRegistry(ledger)
    projection = Projection(ledger)
    random_source = random.Random(2718)
    sequence = 0
    assignment_results = {}
    coding_count = 0
    model_calls = 0

    def rollout(task, arm, phase, skill, occurrence=None):
        nonlocal sequence, coding_count, model_calls
        sequence += 1
        attempt = occurrence or f"{sequence:04d}-{phase}-{arm}-{task}"
        ledger.create_attempt(
            attempt, task, arm, phase, time.time() + 600, "mock-policy", "mock-campaign"
        )
        ledger.transition(attempt, State.RUNNING, attempt + "-start")
        coding = attempt + "-coding"
        ledger.create_lease(coding, attempt, "coding", "mock-image", time.time() + 300, 0)
        ledger.observe_lease(coding, "ACTIVE", coding, coding + "-observed")
        request = json.dumps({"task": task, "skill": skill.decode(), "mock": True}).encode()
        envelope = ledger.admit(
            attempt, attempt + "-model", digest(request), "mock-policy", 0, 0, "model", 1000
        )
        result = gateway.dispatch(envelope, request, "offline")
        assert result["status"] == "COMPLETE"
        model_calls += 1
        ledger.observe_lease(coding, "ABSENT", coding, coding + "-absent")
        ledger.settle("lease:" + coding, 0, coding, coding + "-cash")
        ledger.transition(attempt, State.EVALUATING, attempt + "-evaluate")
        scoring = attempt + "-scoring"
        ledger.create_lease(scoring, attempt, "scoring", "mock-image", time.time() + 300, 0)
        ledger.observe_lease(scoring, "ACTIVE", scoring, scoring + "-observed")
        action = ledger.admit(
            attempt, attempt + "-score", digest(b"mock checks"), "mock-policy", 0, 0, "score", 0
        )
        ledger.before_dispatch(action)
        success = random_source.random() < 0.5
        oracle = OracleResult(
            expected_checks=2,
            completed_checks=2,
            outcomes=("PASS", "PASS") if success else ("FAIL", "PASS"),
            evaluator_hash="mock-evaluator",
        )
        ledger.settle(action.reservation_id, 0, scoring, scoring + "-score-cash")
        ledger.observe_lease(scoring, "ABSENT", scoring, scoring + "-absent")
        ledger.settle("lease:" + scoring, 0, scoring, scoring + "-cash")
        ledger.transition(
            attempt, State.SUCCEEDED if success else State.FAILED, attempt + "-terminal", oracle
        )
        visibility = (
            "final" if phase == "final" else ("private" if phase == "selection" else "development")
        )
        trace = json.dumps(
            {"mock": True, "attempt": attempt, "claim": "mock repair", "verified": success}
        ).encode()
        reference = artifacts.put(trace)
        ledger.register_artifact(reference, visibility, attempt)
        coding_count += 1
        assignment_results[attempt] = success
        return success, 1000, Evidence(reference, task, arm, "synthetic discovery " + str(success))

    try:
        baseline = b"Mock baseline S."
        _, baseline_hash, baseline_tokens = canonical_skill(baseline, len)
        for i in range(8):
            rollout(f"smoke-{i}", "S", "smoke", baseline)
        discovery = [rollout(f"discovery-{i}", "S", "discovery", baseline)[2] for i in range(24)]
        selection = [rollout(f"selection-{i}", "S", "selection", baseline) for i in range(16)]
        vector = SelectionVector(tuple(x[0] for x in selection), sum(x[1] for x in selection))
        context = {key: "mock-" + key for key in REUSE_FIELDS}
        context["campaign"] = "mock-campaign"
        registry.store_selection(reuse_key(baseline_hash, context), baseline_hash, vector)
        s = Candidate(baseline, baseline_hash, baseline_tokens, 0, vector)
        best_b = best_r = s
        r_discovery = []
        proposal_count = 0
        policy = PackingPolicy(30000, 1200, 0, 40000, "mock-whole-record-v1", "mock-byte-count")
        # History packing is covered by separate fixtures; this campaign freezes zero history space.
        for round_number in (1, 2, 3):
            if round_number > 1:
                start = (round_number - 1) * 8
                r_discovery += [
                    rollout(f"discovery-{i}", "R", "discovery", best_r.content)[2]
                    for i in range(start, start + 8)
                ]
            evidence = discovery[: round_number * 8] + r_discovery
            packets = {
                arm: pack(
                    round_number,
                    arm,
                    evidence,
                    baseline if arm == "B" else best_r.content,
                    [],
                    policy,
                    len,
                )
                for arm in ["B", "R"]
            }
            assert packets["B"].common == packets["R"].common
            for arm in ["B", "R"]:
                parent = s if arm == "B" else best_r
                registry.consume_slot(
                    "mock-campaign",
                    round_number,
                    arm,
                    packets[arm].digest,
                    parent.content_hash,
                    2000,
                )
                proposal_count += 1
                content = f"Mock skill {arm}, round {round_number}.".encode()
                resolved = registry.resolve_slot(
                    "mock-campaign", round_number, arm, content, len, context
                )
                assert resolved["status"] == "NEEDS_SELECTION"
                ledger.settle(
                    f"proposal:mock-campaign:{round_number}:{arm}",
                    2000,
                    f"mock-proposal-{proposal_count}",
                    f"proposal-cash-{proposal_count}",
                )
                panel = [rollout(f"selection-{i}", arm, "selection", content) for i in range(16)]
                vector = SelectionVector(tuple(x[0] for x in panel), sum(x[1] for x in panel))
                registry.store_selection(resolved["reuse_key"], resolved["content_hash"], vector)
                candidate = Candidate(
                    content, resolved["content_hash"], len(content), proposal_count, vector
                )
                if arm == "B" and candidate.rank < best_b.rank:
                    best_b = candidate
                if arm == "R" and candidate.rank < best_r.rank:
                    best_r = candidate
        assignments = [
            {"id": f"final-{i}-{arm}", "task": f"final-task-{i}", "arm": arm}
            for i in range(40)
            for arm in ["S", "B", "R"]
        ]
        projection.freeze_schedule(assignments)
        skills = {"S": s, "B": best_b, "R": best_r}
        final = {}
        for item in assignments:
            outcome = rollout(
                item["task"], item["arm"], "final", skills[item["arm"]].content, item["id"]
            )
            final[(item["task"], item["arm"])] = outcome
        assert all(
            set(row) == {"id", "phase", "state"}
            for row in projection.runs()
            if row["phase"] == "final"
        )
        assert not any("final-task-" in event["body"] for event in projection.events())
        projection.release([x["id"] for x in assignments], "mock-release")
        rows = [
            FinalOutcome(
                f"final-task-{i}",
                f"mock-family-{i}",
                *[final[(f"final-task-{i}", arm)][0] for arm in ["S", "B", "R"]],
                *[final[(f"final-task-{i}", arm)][1] for arm in ["S", "B", "R"]],
            )
            for i in range(40)
        ]
        report = screening_report(
            rows,
            [x.task for x in rows],
            {arm: c.content_hash for arm, c in skills.items()},
            integrity_valid=False,
            reconciled=True,
            invalidity_flags=["SYNTHETIC_MOCK_ONLY"],
            research_cash=ledger.money()["settled"],
        )
        result = {
            "mock_only": True,
            "coding_rollouts": coding_count,
            "coding_model_calls": model_calls,
            "proposal_calls": proposal_count,
            "money_micro_usd": ledger.money(),
            "worker_occupancy": ledger.occupied_workers(),
            "live_ready": False,
            "report": report,
        }
        (args.output / "mock-report.json").write_text(json.dumps(result, indent=2) + "\n")
        ledger.backup(args.output / "backup", artifacts)
        assert coding_count == 280 and proposal_count == 6 and ledger.money()["reserved"] == 0
        print(json.dumps({k: v for k, v in result.items() if k != "report"}, indent=2))
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
