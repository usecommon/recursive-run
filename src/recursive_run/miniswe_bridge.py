"""Pinned mini-swe-agent behavior through admitted model/worker ports only.

Never instantiate mini-swe-agent's LocalEnvironment or vendor model classes. Their
provider routes, automatic retries and environment-template expansion are outside
this controller's admission authority.
"""

import importlib.metadata
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from recursive_run.artifacts import digest
from recursive_run.contracts import Manifest
from recursive_run.ledger import Blocked

PINNED_VERSION = "2.4.6"


def load_default_agent(config_root: Path):
    if importlib.metadata.version("mini-swe-agent") != PINNED_VERSION:
        raise Blocked("mini-swe-agent version differs from frozen adapter")
    config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    empty_config = tempfile.mkdtemp(prefix="miniswe-empty-", dir=config_root)
    previous = {
        name: os.environ.get(name) for name in ["MSWEA_GLOBAL_CONFIG_DIR", "MSWEA_SILENT_STARTUP"]
    }
    os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = empty_config
    os.environ["MSWEA_SILENT_STARTUP"] = "1"
    try:
        from minisweagent.agents.default import DefaultAgent
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return DefaultAgent


class AdmittedModel:
    def __init__(
        self,
        ledger,
        gateway,
        attempt_id,
        policy,
        epoch,
        generation,
        maximum_call_cash: int,
        operation: str,
        request_builder: Callable,
        response_parser: Callable,
    ):
        self.ledger, self.gateway = ledger, gateway
        self.attempt_id, self.policy = attempt_id, policy
        self.epoch, self.generation = epoch, generation
        self.maximum_call_cash = maximum_call_cash
        self.operation = operation
        self.request_builder, self.response_parser = request_builder, response_parser
        self.config = {"route": "controller-gateway-only"}

    def query(self, messages, **kwargs):
        if kwargs:
            raise Blocked("unfrozen query overrides prohibited")
        request = self.request_builder(messages)
        if type(request) is not bytes:
            raise ValueError("request builder must return bounded bytes")
        step = self.ledger.attempt(self.attempt_id)["steps"]
        key = f"{self.attempt_id}:model:{step}"
        admitted = self.ledger.admit(
            self.attempt_id,
            key,
            digest(request),
            self.policy,
            self.epoch,
            self.generation,
            "model",
            self.maximum_call_cash,
        )
        result = self.gateway.dispatch(admitted, request, self.operation)
        if result["status"] != "COMPLETE":
            raise Blocked("model operation did not complete; do not retry uncertain calls")
        message = self.response_parser(result["result"].output)
        if type(message) is not dict or message.get("role") != "assistant":
            raise ValueError("malformed model envelope")
        message.setdefault("extra", {})["cost"] = result["result"].actual_cash / 1_000_000
        return message

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [
            {"role": "user", "content": json.dumps(output, sort_keys=True)} for output in outputs
        ]

    def get_template_vars(self, **kwargs):
        return {}  # Never expose controller environment/credentials to prompt templates.

    def serialize(self):
        return {"info": {"model_adapter": "controller-gateway-only"}}


class AdmittedEnvironment:
    def __init__(
        self,
        ledger,
        runner,
        attempt_id,
        policy,
        epoch,
        generation,
        maximum_action_cash,
        maximum_action_bytes=65536,
        maximum_output_bytes=1048576,
    ):
        self.ledger, self.runner = ledger, runner
        self.attempt_id, self.policy = attempt_id, policy
        self.epoch, self.generation = epoch, generation
        self.maximum_action_cash = maximum_action_cash
        self.maximum_action_bytes = maximum_action_bytes
        self.maximum_output_bytes = maximum_output_bytes
        self.sequence = 0
        self.config = {"route": "admitted-worker-only"}

    def execute(self, action, cwd=""):
        if (
            cwd
            or type(action) is not dict
            or set(action) - {"command"}
            or type(action.get("command")) is not str
        ):
            raise Blocked("unsupported/unfrozen worker action")
        payload = json.dumps(action, sort_keys=True).encode()
        if len(payload) > self.maximum_action_bytes:
            raise Blocked("action exceeds bound")
        key = f"{self.attempt_id}:tool:{self.sequence}"
        self.sequence += 1
        admitted = self.ledger.admit(
            self.attempt_id,
            key,
            digest(payload),
            self.policy,
            self.epoch,
            self.generation,
            "tool",
            self.maximum_action_cash,
        )
        self.ledger.before_dispatch(admitted)
        # Runner must execute only in the identified isolated worker and return an attributed receipt.
        result = self.runner.execute(admitted, action)
        if set(result) != {"output", "returncode", "actual_cash", "operation_id"}:
            raise Blocked("invalid runner receipt; retain reservation")
        self.ledger.settle(
            admitted.reservation_id, result["actual_cash"], result["operation_id"], key + "-settled"
        )
        if len(result["output"].encode()) > self.maximum_output_bytes:
            raise Blocked("worker output exceeds bound")
        output = {"output": result["output"], "returncode": result["returncode"]}
        lines = result["output"].lstrip().splitlines(keepends=True)
        if (
            lines
            and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            and result["returncode"] == 0
        ):
            from minisweagent.exceptions import Submitted

            raise Submitted(
                {
                    "role": "exit",
                    "content": "".join(lines[1:]),
                    "extra": {"exit_status": "Submitted", "submission": "".join(lines[1:])},
                }
            )
        return output

    def get_template_vars(self, **kwargs):
        return {
            "cwd": "/task"
        }  # Worker path only; no os.environ, platform.uname or private metadata.

    def serialize(self):
        return {"info": {"environment_adapter": "admitted-worker-only"}}


def run_attempt(
    manifest: Manifest,
    operation: str,
    model: AdmittedModel,
    environment: AdmittedEnvironment,
    task: str,
    skill: str,
    templates: dict[str, str],
    config_root: Path,
    max_format_errors: int = 1,
):
    # Frozen per protocol: consecutive malformed replies tolerated before the attempt ends.
    # Each malformed reply is still an admitted, billed model step within the 20-step cap.
    if type(max_format_errors) is not int or not 1 <= max_format_errors <= 3:
        raise ValueError("format-error tolerance must be a frozen value from 1 to 3")
    blockers = manifest.blockers(operation)
    if blockers:
        raise Blocked("; ".join(blockers))
    if set(templates) != {"system_template", "instance_template"}:
        raise ValueError("freeze both templates")
    DefaultAgent = load_default_agent(config_root)
    agent = DefaultAgent(
        model,
        environment,
        **templates,
        step_limit=20,
        cost_limit=0.5,
        wall_time_limit_seconds=600,
        max_consecutive_format_errors=max_format_errors,
        output_path=None,
    )
    # Each invocation constructs a fresh agent/session. Submitted is a claim; scoring stays external.
    result = agent.run(task=task, skill=skill)
    if result.get("exit_status") == "NEEDS_INPUT":
        result = {**result, "mapped_outcome": "FAILED"}
    return {"claim": result, "trace": agent.serialize(), "verified_success": False}
