"""Controlled dispatch ports. Live bindings are mandatory; no implicit transport retries.

This is an internal adapter contract, not an assertion about any vendor SDK API.
A concrete vendor integration must pin prices, parsing and reconciliation capabilities.
"""

from dataclasses import dataclass
from typing import Protocol

from recursive_run.artifacts import digest
from recursive_run.contracts import AdmittedAction, Manifest
from recursive_run.ledger import Blocked


@dataclass(frozen=True)
class DispatchCapabilities:
    lookup: bool
    idempotent_replay: bool
    sdk_retries_disabled: bool
    protocol_hash: str


@dataclass(frozen=True)
class ModelResult:
    provider_operation_id: str
    output: bytes
    actual_cash: int
    usage: dict[str, int]


class DefiniteNonexecution(Exception):
    """Only use when the frozen provider protocol establishes no dispatch/billing occurred."""


class UncertainDispatch(Exception):
    """Obligation remains reserved. No automatic duplicate request."""


class ProviderAdapter(Protocol):
    capabilities: DispatchCapabilities

    def generate(self, admitted: AdmittedAction, request: bytes) -> ModelResult: ...


class Gateway:
    def __init__(
        self, ledger, manifest: Manifest, adapter: ProviderAdapter, maximum_output_bytes: int
    ):
        if not adapter.capabilities.sdk_retries_disabled:
            raise Blocked("adapter must disable implicit retries")
        if maximum_output_bytes <= 0:
            raise ValueError("positive output bound required")
        self.ledger = ledger
        self.manifest = manifest
        self.adapter = adapter
        self.maximum_output_bytes = maximum_output_bytes

    def dispatch(self, admitted: AdmittedAction, request: bytes, operation: str):
        blockers = self.manifest.blockers(operation)
        if blockers:
            raise Blocked("; ".join(blockers))
        if admitted.action_digest != digest(request):
            raise Blocked("request differs from admitted digest")
        self.ledger.before_dispatch(admitted)
        try:
            result = self.adapter.generate(admitted, request)
        except DefiniteNonexecution:
            self.ledger.settle(
                admitted.reservation_id,
                0,
                admitted.operation_key,
                admitted.operation_key + "-nonexecution",
            )
            return {"status": "DEFINITE_NONEXECUTION"}
        except Exception:
            # Includes timeouts, protocol errors and uncertain provider replies. Never resend.
            with self.ledger.transaction():
                self.ledger._event(
                    admitted.attempt_id,
                    "uncertain_dispatch",
                    {"operation_key": admitted.operation_key},
                )
            return {"status": "UNCERTAIN", "reservation": admitted.reservation_id}
        if (
            type(result.actual_cash) is not int
            or result.actual_cash < 0
            or not result.provider_operation_id
        ):
            return {"status": "UNCERTAIN", "reservation": admitted.reservation_id}
        self.ledger.settle(
            admitted.reservation_id,
            result.actual_cash,
            result.provider_operation_id,
            admitted.operation_key + "-settled",
        )
        if len(result.output) > self.maximum_output_bytes:
            return {"status": "INVALID_OUTPUT", "actual_cash": result.actual_cash}
        return {"status": "COMPLETE", "result": result}
