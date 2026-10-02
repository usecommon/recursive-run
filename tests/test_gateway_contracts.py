import time
import pytest
from recursive_run.artifacts import digest
from recursive_run.contracts import Manifest, State
from recursive_run.gateway import Gateway, DispatchCapabilities, ModelResult, DefiniteNonexecution
from recursive_run.ledger import Ledger, Blocked


class FakeProvider:
    capabilities = DispatchCapabilities(False, False, True, "mock-protocol")

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def generate(self, admitted, request):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_null_live_bindings_fail_closed_by_stage():
    manifest = Manifest(experiment_id="test", stage="RL0", mode="mock")
    assert manifest.blockers("offline") == []
    assert "mode must be live" in manifest.blockers("smoke")
    assert "unbound:smoke_passed" not in manifest.blockers("smoke")
    assert "unbound:smoke_passed" in manifest.blockers("learning")
    assert "unbound:execution_authorized" in manifest.blockers("smoke")


@pytest.mark.parametrize(
    "response, status, remaining",
    [
        (TimeoutError("ambiguous"), "UNCERTAIN", 100),
        (DefiniteNonexecution("not dispatched"), "DEFINITE_NONEXECUTION", 0),
        (ModelResult("provider-id", b"result", 50, {"input": 2, "output": 1}), "COMPLETE", 0),
    ],
)
def test_mock_dispatch_never_retries(tmp_path, response, status, remaining):
    ledger = Ledger(tmp_path / "db")
    try:
        ledger.create_attempt("a", "mock-task", "S", "smoke", time.time() + 600, "p", "e")
        ledger.transition("a", State.RUNNING, "start")
        ledger.create_lease("worker", "a", "coding", "mock-image", time.time() + 100, 0)
        ledger.observe_lease("worker", "ACTIVE", "mock-worker", "worker-seen")
        envelope = ledger.admit("a", "call", digest(b"request"), "p", 0, 0, "model", 100)
        provider = FakeProvider(response)
        manifest = Manifest(experiment_id="e", stage="RL0", mode="mock")
        gateway = Gateway(ledger, manifest, provider, 1000)
        assert gateway.dispatch(envelope, b"request", "offline")["status"] == status
        assert provider.calls == 1 and ledger.money()["reserved"] == remaining
        with pytest.raises(Blocked):
            gateway.dispatch(envelope, b"request", "offline")
        assert provider.calls == 1
    finally:
        ledger.close()
