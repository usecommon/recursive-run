import importlib.util
import json
import time
import pytest
from recursive_run.contracts import Manifest, State
from recursive_run.gateway import Gateway, DispatchCapabilities, ModelResult
from recursive_run.ledger import Ledger, Blocked
from recursive_run.miniswe_bridge import AdmittedModel, AdmittedEnvironment, run_attempt

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("minisweagent") is None, reason="install the pinned execution extra"
)


class Provider:
    capabilities = DispatchCapabilities(False, False, True, "mock-protocol")

    def __init__(self, actions):
        self.actions = actions
        self.calls = 0

    def generate(self, admitted, request):
        self.calls += 1
        assert b"PRIVATE-CANARY" not in request
        message = {"role": "assistant", "content": "mock", "extra": {"actions": self.actions}}
        return ModelResult("mock-" + admitted.operation_key, json.dumps(message).encode(), 10, {})


class Runner:
    def __init__(self, ledger, cancel_after=False):
        self.ledger = ledger
        self.calls = 0
        self.cancel_after = cancel_after

    def execute(self, admitted, action):
        self.calls += 1
        if self.cancel_after:
            self.ledger.cancel(admitted.attempt_id, "cancel-after-first-action")
        output = (
            "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nagent claim only\n"
            if action["command"] == "submit"
            else "done"
        )
        return {
            "output": output,
            "returncode": 0,
            "actual_cash": 0,
            "operation_id": admitted.operation_key,
        }


def setup(tmp_path, actions, cancel=False):
    ledger = Ledger(tmp_path / "db")
    ledger.create_attempt("a", "mock-task", "S", "smoke", time.time() + 600, "p", "e")
    ledger.transition("a", State.RUNNING, "start")
    ledger.create_lease("worker", "a", "coding", "mock-image", time.time() + 500, 0)
    ledger.observe_lease("worker", "ACTIVE", "mock-worker", "worker-seen")
    manifest = Manifest(experiment_id="e", stage="RL0", mode="mock")
    provider = Provider(actions)
    gateway = Gateway(ledger, manifest, provider, 1000)
    model = AdmittedModel(
        ledger,
        gateway,
        "a",
        "p",
        0,
        0,
        100,
        "offline",
        lambda messages: json.dumps(messages).encode(),
        json.loads,
    )
    runner = Runner(ledger, cancel)
    environment = AdmittedEnvironment(ledger, runner, "a", "p", 0, 0, 0)
    return ledger, manifest, provider, model, runner, environment


def test_pinned_agent_uses_gateway_and_submission_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_KEY_FOR_TEST", "PRIVATE-CANARY")
    ledger, manifest, provider, model, runner, environment = setup(
        tmp_path, [{"command": "submit"}]
    )
    try:
        result = run_attempt(
            manifest,
            "offline",
            model,
            environment,
            "mock task",
            "mock skill",
            {"system_template": "Autonomous repair. {{skill}}", "instance_template": "{{task}}"},
            tmp_path / "config",
        )
        assert result["claim"]["exit_status"] == "Submitted"
        assert result["verified_success"] is False
        assert provider.calls == 1 and runner.calls == 1
        assert ledger.attempt("a")["state"] == "RUNNING"
        assert environment.get_template_vars() == {"cwd": "/task"}
    finally:
        ledger.close()


def test_multi_action_reply_rechecks_cancellation(tmp_path):
    ledger, manifest, provider, model, runner, environment = setup(
        tmp_path, [{"command": "first"}, {"command": "stale"}], True
    )
    try:
        with pytest.raises(Blocked):
            run_attempt(
                manifest,
                "offline",
                model,
                environment,
                "mock",
                "skill",
                {"system_template": "repair", "instance_template": "{{task}}"},
                tmp_path / "config",
            )
        assert provider.calls == 1 and runner.calls == 1
        assert ledger.attempt("a")["state"] == "CANCELLED"
    finally:
        ledger.close()


@pytest.mark.parametrize("tolerance", [0, 4, 2.0])
def test_format_error_tolerance_is_bounded(tmp_path, tolerance):
    ledger, manifest, provider, model, runner, environment = setup(tmp_path, [{"command": "x"}])
    try:
        with pytest.raises(ValueError):
            run_attempt(
                manifest,
                "offline",
                model,
                environment,
                "t",
                "s",
                {"system_template": "s", "instance_template": "{{task}}"},
                tmp_path / "config",
                max_format_errors=tolerance,
            )
        assert provider.calls == 0
    finally:
        ledger.close()
