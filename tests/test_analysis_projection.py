import time
import pytest
from recursive_run.analysis import FinalOutcome, screening_report
from recursive_run.ledger import Ledger, Blocked
from recursive_run.contracts import State
from recursive_run.projections import Projection
from recursive_run.artifacts import ArtifactStore


def rows():
    # 40 independent synthetic families. 20 S, 22 B, 24 R successes, no S losses.
    return [FinalOutcome(str(i), str(i), i < 20, i < 22, i < 24, 100, 100, 100) for i in range(40)]


def test_screening_thresholds_and_frozen_bootstrap():
    data = rows()
    kwargs = dict(
        assigned_tasks=[x.task for x in data],
        hashes={"S": "s", "B": "b", "R": "r"},
        sampling_justified=True,
        integrity_valid=True,
        reconciled=True,
    )
    report = screening_report(data, **kwargs)
    assert report["state"] == "PROMISING_FOR_CONFIRMATION"
    assert report["R-S"]["supplemental_exact_mcnemar_p"] == 0.125
    assert report["intervals"]["seed"] == 2718
    assert report["R-S"]["difference"] == 0.1 and report["R-B"]["difference"] == 0.05
    assert report["assigned_denominator"] == 40
    assert (
        screening_report(data, **{**kwargs, "hashes": {"S": "s", "B": "b", "R": "s"}})["state"]
        == "INCONCLUSIVE"
    )
    assert screening_report(data, **{**kwargs, "integrity_valid": False})["state"] == "STOP"
    assert screening_report(data[:-1], **kwargs)["state"] == "INCONCLUSIVE"


def test_undefined_zero_success_and_unjustified_sampling():
    data = [FinalOutcome(str(i), str(i), False, False, False, 100, 100, 100) for i in range(40)]
    report = screening_report(
        data,
        [x.task for x in data],
        {"S": "s", "B": "b", "R": "r"},
        integrity_valid=True,
        reconciled=True,
    )
    assert report["state"] == "INCONCLUSIVE" and report["R_to_S_cost_per_success"] is None
    assert report["intervals"]["status"] == "UNESTIMABLE"
    assert report["R-S"]["supplemental_exact_mcnemar_p"] is None


def test_sealing_and_release_obligations(tmp_path):
    ledger = Ledger(tmp_path / "db")
    try:
        projection = Projection(ledger)
        assignments = [
            {"id": f"{i}-{arm}", "task": str(i), "arm": arm}
            for i in range(40)
            for arm in ["S", "B", "R"]
        ]
        assignments[2]["id"] = "final"
        projection.freeze_schedule(assignments)
        for item in assignments:
            ledger.create_attempt(
                item["id"], item["task"], item["arm"], "final", time.time() + 600, "p", "e"
            )
            if item["id"] != "final":
                ledger.cancel(item["id"], item["id"] + "-cancel")
        ledger.transition("final", State.RUNNING, "start")
        ledger.create_lease("worker", "final", "coding", "mock-image", time.time() + 100, 0)
        ledger.observe_lease("worker", "ACTIVE", "mock-worker", "worker-seen")
        action = ledger.admit("final", "operation", "secret-digest", "p", 0, 0, "model", 100)
        ledger.transition("final", State.FAILED, "done")
        store = ArtifactStore(tmp_path / "artifacts")
        private = store.put(b"PRIVATE-CANARY")
        final = store.put(b"FINAL-CANARY")
        ledger.register_artifact(private, "private")
        ledger.register_artifact(final, "final", "final")
        projection = Projection(ledger)
        assert len(projection.runs()) == 120
        assert all(
            x["state"] == "TERMINAL" and set(x) == {"id", "phase", "state"}
            for x in projection.runs()
        )
        assert projection.events() == []
        assert not projection.artifact_allowed(private)
        assert not projection.artifact_allowed(final)
        assert not projection.artifact_allowed(final, learner=True)
        with pytest.raises(Blocked):
            projection.release(["final"], "release")
        ledger.settle(action.reservation_id, 90, "provider", "charge")
        ledger.observe_lease("worker", "ABSENT", "mock-worker", "worker-gone")
        ledger.settle("lease:worker", 0, "mock-worker", "worker-charge")
        projection.release([x["id"] for x in assignments], "release", incomplete=True)
        assert projection.artifact_allowed(final)
        assert not projection.artifact_allowed(private)
        assert not projection.artifact_allowed(final, learner=True)
        assert next(x for x in projection.runs() if x["id"] == "final")["state"] == "FAILED"
    finally:
        ledger.close()


def test_replicated_report_uses_task_means_and_majority_flips():
    from recursive_run.analysis import ReplicatedOutcome, replicated_screening_report

    T, F = True, False
    rows = [
        ReplicatedOutcome(f"t{i}", f"f{i}", (F, F, F), (F, F, T), (T, T, F), 30, 30, 30)
        for i in range(10)
    ] + [
        ReplicatedOutcome(f"u{i}", f"g{i}", (T, T, T), (T, T, T), (T, T, T), 30, 30, 30)
        for i in range(10)
    ]
    assigned = [r.task for r in rows]
    report = replicated_screening_report(
        rows,
        assigned,
        {"S": "s", "B": "b", "R": "r"},
        3,
        sampling_justified=True,
        integrity_valid=True,
        reconciled=True,
    )
    # R-S: ten tasks move 0 -> 2/3, ten unchanged: mean difference 1/3.
    assert abs(report["R-S"]["mean_rate_difference"] - 1 / 3) < 1e-9
    assert report["R-S"]["majority_wins"] == 10 and report["R-S"]["majority_losses"] == 0
    assert report["intervals"]["unit"].startswith("task")
    assert report["state"] == "PROMISING_FOR_CONFIRMATION"


def test_replicated_report_rejects_mixed_replicate_counts():
    import pytest
    from recursive_run.analysis import ReplicatedOutcome, replicated_screening_report

    with pytest.raises(ValueError):
        ReplicatedOutcome("t", "f", (True,), (True, False), (True,), 0, 0, 0)
    row = ReplicatedOutcome("t", "f", (True, True), (True, True), (True, True), 1, 1, 1)
    with pytest.raises(ValueError):
        replicated_screening_report([row], ["t"], {"S": "s", "B": "b", "R": "r"}, 3)
