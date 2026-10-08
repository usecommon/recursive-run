"""Live E2B feasibility probe: one offline sandbox, bounded commands, provider expiry.

Spends at most one 60-second sandbox under a private probe ledger capped at $0.01. It
checks the three smoke bindings still marked unverified in configs/smoke-authorization.json:
commands through the managed proxy, blocked egress, and provider-enforced expiry (the
sandbox is never killed by this script; absence must come from E2B's own lifetime).
The receipt holds allowlisted protocol facts only, never keys or tokens.
"""

import argparse
import json
import time
from pathlib import Path

import httpx

from recursive_run.contracts import State
from recursive_run.ledger import Ledger
from recursive_run.providers.e2b import E2BRunner, from_authorization


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--authorization", type=Path, default=Path("configs/smoke-authorization.json")
    )
    parser.add_argument("--template", default="base", help="probe only; tasks need a pinned build")
    parser.add_argument("--lifetime", type=int, default=60)
    parser.add_argument("--output", type=Path, default=Path(".runtime/e2b-live-probe.json"))
    args = parser.parse_args()
    spec, _, _ = from_authorization(args.authorization)
    spec = type(spec)(
        args.template, spec.vcpu, spec.memory_gib, spec.usd_per_vcpu_second, spec.usd_per_gib_second
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(
        args.output.with_suffix(f".{time.time_ns()}.db"),
        cap=10_000,
        category_caps={"coding_model": 0, "proposal": 0, "infrastructure": 10_000},
    )
    runner = E2BRunner(ledger, spec)
    attempt = f"probe-{time.time_ns()}"
    ledger.create_attempt(attempt, "e2b-probe", "S", "smoke", time.time() + 600, "probe", "probe")
    ledger.transition(attempt, State.RUNNING, attempt + "-start")
    receipt = {"schema_version": 1, "template": args.template, "lifetime_seconds": args.lifetime}
    lease = attempt + "-lease"
    started = time.time()
    receipt["create"] = runner.create(lease, attempt, "coding", args.lifetime)
    if receipt["create"] != "ACTIVE":
        receipt["reconcile"] = runner.reconcile(lease, started + args.lifetime)
    if lease in runner.sessions:
        hello = runner.run(lease, "echo rl-ok; python3 --version", timeout=30, maximum_output=4096)
        receipt["command"] = {
            "status": hello.status,
            "stdout_ok": hello.stdout.startswith(b"rl-ok\n"),
            "python": hello.stdout.decode(errors="replace").splitlines()[-1:],
        }
        # Connect plus an actual exchange: an accepted TCP handshake alone could be a
        # firewall artifact, but returned HTTP bytes prove egress.
        probe = (
            'python3 -c "import socket\n'
            "try:\n"
            " s = socket.create_connection(('1.1.1.1', 80), timeout=4)\n"
            " s.sendall(b'HEAD / HTTP/1.0\\r\\nHost: one.one.one.one\\r\\n\\r\\n')\n"
            " data = s.recv(64)\n"
            " print('LEAK' if data else 'CONNECT_ONLY', len(data))\n"
            "except OSError as e: print('BLOCKED', type(e).__name__)\""
        )
        net = runner.run(lease, probe, timeout=20, maximum_output=4096)
        receipt["egress"] = {
            "status": net.status,
            "stdout": net.stdout.decode(errors="replace")[:80],
        }
        sandbox_id = runner.sessions[lease]["id"]
        # Wait for E2B's own lifetime to end the sandbox; never send a kill here.
        absent_at = None
        while time.time() < started + args.lifetime + 90:
            check = runner._api("GET", f"/sandboxes/{sandbox_id}")
            if check.status_code == 404:
                absent_at = time.time()
                break
            time.sleep(5)
        receipt["expiry"] = {
            "absent_after_seconds": None if absent_at is None else round(absent_at - started, 1),
            "within_lifetime_plus_grace": absent_at is not None
            and absent_at <= started + args.lifetime + 30,
        }
        if absent_at is not None:
            runner.release(lease)  # records verified absence and settles the bounded charge
    receipt["ledger"] = ledger.money()
    receipt["occupied_workers"] = ledger.occupied_workers()
    egress = receipt.get("egress", {}).get("stdout", "").split()
    # A control sandbox with internet allowed returned 64 bytes to this check; with the
    # deny settings E2B accepts the handshake but no data flows ("CONNECT_ONLY 0").
    egress_blocked = egress[:1] == ["BLOCKED"] or egress[:2] == ["CONNECT_ONLY", "0"]
    receipt["passed"] = bool(
        receipt.get("command", {}).get("stdout_ok")
        and egress_blocked
        and receipt.get("expiry", {}).get("within_lifetime_plus_grace")
    )
    ledger.close()
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    raise SystemExit(0 if receipt["passed"] else 2)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as error:
        raise SystemExit(f"transport error: {type(error).__name__}")
