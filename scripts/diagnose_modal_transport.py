"""Read-only Modal protocol probe; prints allowlisted metadata, never credentials.

Run with: uv run --extra cloud python scripts/diagnose_modal_transport.py
No workers, apps, model generations, implicit retries, or proxy bypasses.
"""

import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path

from google.protobuf import empty_pb2
from grpclib.client import UnaryUnaryMethod
from h2.connection import H2Connection
from modal_proto import api_grpc

from recursive_run.providers.modal_client import SystemTrustModalClient


def header_text(value):
    return value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value)


async def diagnose(host, timeout):
    original = H2Connection.receive_data
    events = []

    def observe(connection, data):
        received = original(connection, data)
        for event in received:
            record = {"type": type(event).__name__}
            if hasattr(event, "stream_id"):
                record["stream_id"] = event.stream_id
            if hasattr(event, "headers"):
                record["headers"] = {
                    header_text(k): header_text(v)
                    for k, v in event.headers
                    if header_text(k) in {":status", "content-type", "grpc-status"}
                }
            if hasattr(event, "data"):
                record["body_bytes"] = len(event.data)
            events.append(record)
        return received

    H2Connection.receive_data = observe
    client = SystemTrustModalClient()
    outcomes = []
    try:
        channel = await client._get_channel("https://" + host)
        methods = [
            ("ClientHello", api_grpc.ModalClientStub(channel).ClientHello),
            (
                "NonexistentDiagnosticMethod",
                UnaryUnaryMethod(
                    channel,
                    "/modal.client.ModalClient/NonexistentDiagnosticMethod",
                    empty_pb2.Empty,
                    empty_pb2.Empty,
                ),
            ),
        ]
        for name, method in methods:
            start = len(events)
            try:
                await asyncio.wait_for(method(empty_pb2.Empty(), timeout=timeout), timeout + 1)
                outcome = "completed"
            except Exception as error:
                outcome = type(error).__name__
            outcomes.append({"method": name, "outcome": outcome, "events": events[start:]})
    finally:
        H2Connection.receive_data = original
        await client._close()
    return {
        "host": host,
        "modal_version": importlib.metadata.version("modal"),
        "grpclib_version": importlib.metadata.version("grpclib"),
        "proxy_routing": "inherited",
        "tls_verification": True,
        "paid_workers_started": 0,
        "calls": outcomes,
        "transport_passed": outcomes[0]["outcome"] == "completed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", choices=["api.modal.com", "api.modal2.com"], default="api.modal.com"
    )
    parser.add_argument("--timeout", type=int, choices=range(1, 31), default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = asyncio.run(diagnose(args.host, args.timeout))
    rendered = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if receipt["transport_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
