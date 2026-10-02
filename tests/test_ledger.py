import time
import pytest
from recursive_run.ledger import Ledger, Blocked
from recursive_run.contracts import State, OracleResult
from recursive_run.artifacts import ArtifactStore


@pytest.fixture
def ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    yield ledger
    ledger.close()


def run(ledger, key="a"):
    ledger.create_attempt(key, key, "S", "smoke", time.time() + 600, "policy", "experiment")
    ledger.transition(key, State.RUNNING, key + "-start")


def active_worker(ledger, key="a"):
    worker = "active-" + key
    if not ledger.db.execute("SELECT 1 FROM leases WHERE id=?", (worker,)).fetchone():
        ledger.create_lease(worker, key, "coding", "mock-image", time.time() + 300, 0)
        ledger.observe_lease(worker, "ACTIVE", worker, worker + "-seen")


def admit(ledger, key="a", operation="call"):
    active_worker(ledger, key)
    return ledger.admit(key, operation, "digest", "policy", 0, 0, "model", 100_000)


def test_atomic_transition_and_receipt(ledger):
    run(ledger)
    assert ledger.transition("a", State.EVALUATING, "eval")["state"] == "EVALUATING"
    assert ledger.transition("a", State.EVALUATING, "eval")["state"] == "EVALUATING"
    with pytest.raises(Blocked):
        ledger.transition("a", State.SUCCEEDED, "done")
    partial = OracleResult(
        expected_checks=2, completed_checks=1, outcomes=("PASS",), evaluator_hash="h"
    )
    with pytest.raises(Blocked):
        ledger.transition("a", State.SUCCEEDED, "done", partial)
    complete = OracleResult(
        expected_checks=2, completed_checks=2, outcomes=("PASS", "PASS"), evaluator_hash="h"
    )
    ledger.transition("a", State.SUCCEEDED, "done", complete)
    with pytest.raises(Blocked):
        ledger.transition("a", State.FAILED, "late")


def test_cancel_fences_late_reply_but_allows_settlement(ledger):
    run(ledger)
    envelope = admit(ledger)
    ledger.before_dispatch(envelope)
    ledger.cancel("a", "cancel")
    assert ledger.money() == {"settled": 0, "reserved": 100_000}
    with pytest.raises(Blocked):
        ledger.before_dispatch(envelope)
    with pytest.raises(Blocked):
        ledger.admit("a", "late", "d", "policy", 0, 0, "tool", 0)
    ledger.settle(envelope.reservation_id, 80_000, "provider-1", "charge")
    ledger.settle(envelope.reservation_id, 80_000, "provider-1", "charge")
    assert ledger.money() == {"settled": 80_000, "reserved": 0}
    assert ledger.attempt("a")["state"] == "CANCELLED"


def test_no_duplicate_dispatch_or_changed_receipt(ledger):
    run(ledger)
    envelope = admit(ledger)
    ledger.before_dispatch(envelope)
    with pytest.raises(Blocked):
        ledger.before_dispatch(envelope)
    with pytest.raises(Blocked):
        admit(ledger)
    ledger.settle(envelope.reservation_id, 1, "provider", "r")
    with pytest.raises(Blocked):
        ledger.settle(envelope.reservation_id, 2, "provider", "r")


def test_uncertain_workers_occupy_capacity_and_handoff(ledger):
    run(ledger, "a")
    run(ledger, "b")
    expiry = time.time() + 100
    ledger.create_lease("unknown", "a", "coding", "image", expiry, 100)
    ledger.create_lease("known", "b", "coding", "image", expiry, 100)
    ledger.observe_lease("known", "ACTIVE", "provider-b", "seen-b")
    assert ledger.occupied_workers() == 2
    ledger.transition("a", State.EVALUATING, "a-eval")
    with pytest.raises(Blocked):
        ledger.create_lease("third", "a", "scoring", "image", expiry, 100)
    ledger.observe_lease("unknown", "ABSENT", "provider-a", "absent-a")
    ledger.create_lease("score-a", "a", "scoring", "image", expiry, 100)
    ledger.observe_lease("score-a", "ABSENT", "provider-score-a", "score-gone")
    ledger.transition("b", State.EVALUATING, "b-eval")
    ledger.observe_lease("known", "RELEASE_PENDING", "provider-b", "release-ack")
    with pytest.raises(Blocked):
        ledger.create_lease("score-b", "b", "scoring", "image", expiry, 100)
    ledger.observe_lease("known", "ABSENT", "provider-b", "b-gone")
    ledger.create_lease("score-b", "b", "scoring", "image", expiry, 100)
    assert ledger.money()["reserved"] == 400


def test_restart_preserves_uncertainty_and_fences(ledger):
    run(ledger)
    envelope = admit(ledger)
    ledger.before_dispatch(envelope)
    ledger.observe_lease("active-a", "RELEASE_PENDING", "active-a", "release-uncertain")
    path = ledger.path
    ledger.close()
    restarted = Ledger(path)
    try:
        unresolved = restarted.recover()
        assert len(unresolved["obligations"]) == 2
        assert restarted.occupied_workers() == 1
        assert restarted.attempt("a")["state"] == "INTERRUPTED"
        with pytest.raises(Blocked):
            restarted.before_dispatch(envelope)
        restarted.settle(envelope.reservation_id, 25, "late", "receipt")
        assert restarted.attempt("a")["state"] == "INTERRUPTED"
    finally:
        restarted.close()
    # Restore fixture connection so teardown is harmless.
    ledger.db = __import__("sqlite3").connect(":memory:")
    ledger.writer_lock = open(str(path) + ".writer", "a+b")


def test_final_protection_and_per_rollout_ceiling(tmp_path):
    ledger = Ledger(tmp_path / "db", cap=1_000_000, final_protected=600_000)
    try:
        run(ledger)
        active_worker(ledger)
        with pytest.raises(Blocked):
            ledger.admit("a", "too-much", "d", "policy", 0, 0, "model", 450_000)
        envelope = admit(ledger)
        ledger.settle(envelope.reservation_id, 100_000, "p", "r")
        with pytest.raises(Blocked):
            ledger.admit("a", "ceiling", "d", "policy", 0, 0, "model", 500_001)
        assert ledger.attempt("a")["steps"] == 1
    finally:
        ledger.close()


def test_single_writer_and_artifact_backup(ledger, tmp_path):
    with pytest.raises(Blocked):
        Ledger(ledger.path)
    store = ArtifactStore(tmp_path / "artifacts")
    key = store.put(b"private expected output")
    ledger.register_artifact(key, "private")
    ledger.backup(tmp_path / "backup", store)
    Ledger.verify_backup(tmp_path / "backup")
    (tmp_path / "backup" / "artifacts" / key).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        Ledger.verify_backup(tmp_path / "backup")


def test_step_ceiling_and_late_policy_epoch(ledger):
    run(ledger)
    active_worker(ledger)
    for index in range(20):
        envelope = ledger.admit("a", str(index), "d", "policy", 0, 0, "model", 0)
        ledger.before_dispatch(envelope)
        ledger.settle(envelope.reservation_id, 0, str(index), "r" + str(index))
    with pytest.raises(Blocked):
        ledger.admit("a", "21", "d", "policy", 0, 0, "model", 0)
    with pytest.raises(Blocked):
        ledger.admit("a", "policy", "d", "new-policy", 0, 0, "tool", 0)
    with pytest.raises(Blocked):
        ledger.admit("a", "expired", "d", "policy", 0, 0, "tool", 0, now=time.time() + 1000)


def test_crash_rolls_back_transition_and_event_atomically(tmp_path):
    import subprocess
    import sys

    ledger = Ledger(tmp_path / "db")
    run(ledger)
    ledger.close()
    code = """
from pathlib import Path
from recursive_run.ledger import Ledger
import os,sys
ledger=Ledger(Path(sys.argv[1]))
ledger.db.execute('BEGIN IMMEDIATE')
ledger.db.execute("UPDATE attempts SET state='FAILED' WHERE id='a'")
ledger._event('a','uncommitted',{})
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "db")])
    assert result.returncode == 9
    restored = Ledger(tmp_path / "db")
    try:
        assert restored.attempt("a")["state"] == "RUNNING"
        assert not restored.db.execute("SELECT 1 FROM events WHERE kind='uncommitted'").fetchone()
        assert restored.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()


def test_global_cancellation_stops_future_attempts_and_keeps_obligations(ledger):
    run(ledger)
    envelope = admit(ledger)
    ledger.before_dispatch(envelope)
    ledger.cancel_experiment("experiment", "stop-experiment")
    with pytest.raises(Blocked):
        ledger.create_attempt("new", "t", "S", "smoke", time.time() + 600, "policy", "experiment")
    with pytest.raises(Blocked):
        ledger.before_dispatch(envelope)
    assert ledger.money()["reserved"] == 100_000
    ledger.settle(envelope.reservation_id, 50, "late-original-call", "late-charge")
    assert ledger.attempt("a")["state"] == "CANCELLED"
