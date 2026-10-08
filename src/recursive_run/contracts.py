"""Versioned, strict internal envelopes. Money is integer micro-USD, never floats."""

from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1


class State(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


TRANSITIONS = {
    State.QUEUED: {State.RUNNING, State.CANCELLED, State.BLOCKED},
    State.RUNNING: {State.EVALUATING, State.FAILED, State.INTERRUPTED, State.CANCELLED},
    State.EVALUATING: {State.SUCCEEDED, State.FAILED, State.INTERRUPTED, State.CANCELLED},
}
TERMINAL = set(State) - set(TRANSITIONS)


class Limits(Record):
    model_steps: int = Field(default=20, ge=1, le=20)
    coding_seconds: int = Field(default=600, ge=1, le=600)
    worker_cap: Literal[2] = 2
    skill_tokens: int = Field(default=1200, ge=1, le=1200)
    patch_bytes: int = Field(default=262144, ge=1)
    output_bytes: int = Field(default=1048576, ge=1)
    external_cash: int = Field(default=200_000_000, ge=0, le=200_000_000)
    smoke_cash: int = Field(default=25_000_000, ge=0, le=25_000_000)
    rollout_model_cash: int = Field(default=500_000, ge=0, le=500_000)
    proposal_cash: int = Field(default=2_000_000, ge=0, le=2_000_000)
    infrastructure_retries: Literal[0] = 0


class Manifest(Record):
    experiment_id: str = Field(min_length=1)
    stage: Literal["RL0", "RL1"]
    mode: Literal["mock", "live"]
    source_revision: str | None = None
    model_version: str | None = None
    model_settings_hash: str | None = None
    adapter_revision: str | None = None
    image_digest: str | None = None
    runner_provider: str | None = None
    expiry_verified: bool = False
    dispatch_protocol_hash: str | None = None
    prices_hash: str | None = None
    durable_storage_verified: bool = False
    authentication_verified: bool = False
    execution_authorized: bool = False
    preparation_limits_hash: str | None = None
    scoring_limits_hash: str | None = None
    oracle_hash: str | None = None
    intake_receipts_hash: str | None = None
    partition_hash: str | None = None
    group_map_hash: str | None = None
    sampling_receipt_hash: str | None = None
    packing_hash: str | None = None
    analysis_hash: str | None = None
    release_policy_hash: str | None = None
    cost_policy_hash: str | None = None
    smoke_passed: bool = False
    # Smoke only: a recorded reason for running before durable storage is certified.
    # Never accepted for learning or final operations.
    storage_waiver: str | None = None
    limits: Limits = Field(default_factory=Limits)

    def blockers(self, operation: str) -> list[str]:
        if operation not in {"offline", "smoke", "learning", "final"}:
            raise ValueError("unknown operation")
        if operation == "offline":
            return []
        required = [
            "source_revision",
            "model_version",
            "model_settings_hash",
            "adapter_revision",
            "image_digest",
            "runner_provider",
            "expiry_verified",
            "dispatch_protocol_hash",
            "prices_hash",
            "durable_storage_verified",
            "authentication_verified",
            "execution_authorized",
            "preparation_limits_hash",
            "scoring_limits_hash",
            "oracle_hash",
            "intake_receipts_hash",
            "cost_policy_hash",
        ]
        if operation in {"learning", "final"}:
            required += [
                "smoke_passed",
                "partition_hash",
                "group_map_hash",
                "sampling_receipt_hash",
                "packing_hash",
                "analysis_hash",
                "release_policy_hash",
            ]
        if operation == "smoke" and self.storage_waiver:
            required.remove("durable_storage_verified")
        return (["mode must be live"] if self.mode != "live" else []) + [
            f"unbound:{key}" for key in required if not getattr(self, key)
        ]


class AdmittedAction(Record):
    experiment_id: str
    attempt_id: str
    operation_key: str
    action_digest: str
    policy_hash: str
    cancellation_epoch: int = Field(ge=0)
    generation: int = Field(ge=0)
    deadline: float
    reservation_id: str
    lease_id: str
    maximum_cost: int = Field(ge=0)
    kind: Literal["model", "tool", "score"]


class OracleResult(Record):
    expected_checks: int = Field(ge=2)
    completed_checks: int = Field(ge=0)
    outcomes: tuple[Literal["PASS", "FAIL", "TIMEOUT", "MALFORMED", "WORKER_ERROR"], ...]
    forbidden_edit: bool = False
    evaluator_hash: str

    @model_validator(mode="after")
    def counts(self):
        if (
            len(self.outcomes) != self.completed_checks
            or self.completed_checks > self.expected_checks
        ):
            raise ValueError("inconsistent check count")
        return self

    @property
    def success(self) -> bool:
        return (
            not self.forbidden_edit
            and self.completed_checks == self.expected_checks
            and all(x == "PASS" for x in self.outcomes)
        )
