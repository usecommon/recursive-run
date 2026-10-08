import base64
import json
import struct
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from recursive_run.contracts import State
from recursive_run.ledger import Blocked, Ledger
from recursive_run.providers.e2b import E2BRunner, SandboxSpec, from_authorization

SPEC = SandboxSpec("tpl-pinned", 2, 2, Decimal("0.000014"), Decimal("0.0000045"))
T0 = time.time()
CONFIG = Path(__file__).parents[1] / "configs" / "smoke-authorization.json"


class FakeE2B:
    """Minimal E2B control plane + envd, recording every request."""

    def __init__(self):
        self.requests = []
        self.sandboxes = {}
        self.create_status = 201
        self.create_raises = False
        self.kill_works = True
        self.cpu = 2
        self.stream = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        path, method = request.url.path, request.method
        if request.url.host == "api.e2b.app":
            assert request.headers["X-API-Key"] == "key"
            if path == "/v2/sandboxes" and method == "POST":
                if self.create_raises:
                    raise httpx.ReadTimeout("lost reply", request=request)
                if self.create_status != 201:
                    return httpx.Response(self.create_status, json={"message": "no"})
                body = json.loads(request.content)
                sandbox_id = f"sbx{len(self.sandboxes)}"
                self.sandboxes[sandbox_id] = body
                return httpx.Response(
                    201,
                    json={
                        "sandboxID": sandbox_id,
                        "templateID": body["templateID"],
                        "clientID": "c",
                        "envdVersion": "0.2",
                        "envdAccessToken": "envd-token",
                        "domain": "e2b.app",
                    },
                )
            if path == "/v2/sandboxes" and method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {"sandboxID": k, "metadata": v["metadata"]}
                        for k, v in self.sandboxes.items()
                    ],
                )
            sandbox_id = path.rsplit("/", 1)[-1]
            if method == "DELETE":
                if self.kill_works:
                    self.sandboxes.pop(sandbox_id, None)
                return httpx.Response(204)
            if method == "GET":
                if sandbox_id not in self.sandboxes:
                    return httpx.Response(404, json={})
                return httpx.Response(200, json={"cpuCount": self.cpu, "memoryMB": 2048})
        assert request.headers["X-Access-Token"] == "envd-token"
        if path == "/process.Process/Start":
            return httpx.Response(200, content=b"".join(self.stream))
        if path == "/files":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})


def frame(message, flags=0):
    body = json.dumps(message).encode()
    return struct.pack(">BI", flags, len(body)) + body


@pytest.fixture
def setup(tmp_path):
    ledger = Ledger(tmp_path / "db", cap=25_000_000, category_caps={"infrastructure": 2_000_000})
    fake = FakeE2B()
    now = [T0]
    runner = E2BRunner(
        ledger,
        SPEC,
        api_key="key",
        client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        clock=lambda: now[0],
    )
    ledger.create_attempt("a1", "task", "S", "smoke", time.time() + 3600, "p", "exp")
    ledger.transition("a1", State.RUNNING, "a1-start")
    yield ledger, fake, runner, now
    ledger.close()


def test_create_binds_lifetime_offline_network_and_lease_identity(setup):
    ledger, fake, runner, _ = setup
    assert runner.create("L1", "a1", "coding", 660) == "ACTIVE"
    body = json.loads(fake.requests[0].content)
    assert body["timeout"] == 660 and body["autoPause"] is False
    assert body["allow_internet_access"] is False
    assert body["metadata"] == {"rl_lease": "L1", "rl_attempt": "a1"}
    assert ledger.occupied_workers() == 1
    # 660 s × (2 vCPU × $0.000014 + 2 GiB × $0.0000045) = $0.02442
    assert ledger.money() == {"settled": 0, "reserved": 24420}


def test_rejected_create_is_definite_nonexecution(setup):
    ledger, fake, runner, _ = setup
    fake.create_status = 429
    assert runner.create("L1", "a1", "coding", 660) == "ABSENT"
    assert ledger.occupied_workers() == 0
    assert ledger.money() == {"settled": 0, "reserved": 0}


def test_uncertain_create_is_reconciled_never_resent(setup):
    ledger, fake, runner, now = setup
    fake.create_raises = True
    assert runner.create("L1", "a1", "coding", 60) == "UNKNOWN"
    assert ledger.occupied_workers() == 1  # unknown creation still holds a slot
    creates = [r for r in fake.requests if r.method == "POST" and r.url.path == "/v2/sandboxes"]
    assert len(creates) == 1
    # Not listed yet, lifetime not over: still unknown.
    assert runner.reconcile("L1", expiry=T0 + 60) == "UNKNOWN"
    # The request had in fact created a sandbox; reconciliation finds it by lease metadata.
    fake.sandboxes["sbx9"] = {"metadata": {"rl_lease": "L1"}}
    assert runner.reconcile("L1", expiry=T0 + 60) == "ACTIVE"
    assert runner.release("L1") and ledger.occupied_workers() == 0


def test_unlisted_after_expiry_settles_full_reservation(setup):
    ledger, fake, runner, now = setup
    fake.create_raises = True
    runner.create("L1", "a1", "coding", 60)
    now[0] = T0 + 61
    assert runner.reconcile("L1", expiry=T0 + 60) == "ABSENT"
    assert ledger.occupied_workers() == 0
    # 60 s lifetime × $0.000037/s = 2220 micro-USD: the whole reservation.
    assert ledger.money() == {"settled": 2220, "reserved": 0}


def test_duplicate_lease_sandboxes_block(setup):
    _, fake, runner, _ = setup
    fake.create_raises = True
    runner.create("L1", "a1", "coding", 60)
    fake.sandboxes["x"] = {"metadata": {"rl_lease": "L1"}}
    fake.sandboxes["y"] = {"metadata": {"rl_lease": "L1"}}
    with pytest.raises(Blocked):
        runner.reconcile("L1", expiry=T0 + 60)


def test_release_requires_verified_absence(setup):
    ledger, fake, runner, now = setup
    runner.create("L1", "a1", "coding", 660)
    fake.kill_works = False
    assert runner.release("L1") is False
    assert ledger.occupied_workers() == 1  # unconfirmed release keeps occupying capacity
    fake.kill_works = True
    now[0] = T0 + 100
    assert runner.release("L1") is True
    assert ledger.occupied_workers() == 0
    # 100 s observed × $0.000037/s = 3700 micro-USD, never above the reservation.
    assert ledger.money() == {"settled": 3700, "reserved": 0}


def test_oversized_template_is_killed_and_blocked(setup):
    ledger, fake, runner, _ = setup
    fake.cpu = 8
    with pytest.raises(Blocked):
        runner.create("L1", "a1", "coding", 660)
    assert ledger.occupied_workers() == 0


def test_worker_cap_is_the_smoke_e2b_subcap(tmp_path):
    ledger = Ledger(tmp_path / "db", cap=25_000_000, category_caps={"infrastructure": 30_000})
    fake = FakeE2B()
    runner = E2BRunner(
        ledger,
        SPEC,
        api_key="key",
        client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )
    ledger.create_attempt("a1", "task", "S", "smoke", time.time() + 3600, "p", "exp")
    ledger.transition("a1", State.RUNNING, "a1-start")
    runner.create("L1", "a1", "coding", 660)
    with pytest.raises(Blocked, match="category cap"):
        runner.create("L2", "a1", "coding", 660)
    assert len([r for r in fake.requests if r.method == "POST"]) == 1
    ledger.close()


def test_run_parses_connect_stream(setup):
    _, fake, runner, _ = setup
    runner.create("L1", "a1", "coding", 660)
    fake.stream = [
        frame({"event": {"start": {"pid": 7}}}),
        frame({"event": {"data": {"stdout": base64.b64encode(b"hello\n").decode()}}}),
        frame({"event": {"end": {"exitCode": 3, "exited": True, "status": "exit status 3"}}}),
        frame({}, flags=0x02),
    ]
    result = runner.run("L1", "echo hello; exit 3", timeout=10, maximum_output=1000)
    assert (result.status, result.exit_code, result.stdout) == ("NONZERO", 3, b"hello\n")
    start = next(r for r in fake.requests if r.url.path == "/process.Process/Start")
    assert start.url.host == "49983-sbx0.e2b.app"
    assert start.headers["Content-Type"] == "application/connect+json"


def test_run_bounds_output_and_kills_process(setup):
    _, fake, runner, _ = setup
    runner.create("L1", "a1", "coding", 660)
    flood = base64.b64encode(b"x" * 2000).decode()
    fake.stream = [
        frame({"event": {"start": {"pid": 9}}}),
        frame({"event": {"data": {"stdout": flood}}}),
    ]
    result = runner.run("L1", "yes", timeout=10, maximum_output=1000)
    assert result.status == "MALFORMED" and len(result.stdout) == 1000
    kill = [r for r in fake.requests if r.url.path == "/process.Process/SendSignal"]
    assert json.loads(kill[0].content) == {"process": {"pid": 9}, "signal": "SIGNAL_SIGKILL"}


def test_stream_error_and_garbage_never_succeed(setup):
    _, fake, runner, _ = setup
    runner.create("L1", "a1", "coding", 660)
    fake.stream = [frame({"error": {"code": "unavailable"}}, flags=0x02)]
    assert runner.run("L1", "true", 10, 1000).status == "TRANSPORT_ERROR"
    fake.stream = [b"\x00\x00\x00\x00\x05nope!"]
    assert runner.run("L1", "true", 10, 1000).status == "MALFORMED"


def test_upload_rejects_unsafe_paths(setup):
    _, _, runner, _ = setup
    runner.create("L1", "a1", "coding", 660)
    for name in ["../escape.py", "/abs.py", ".git/config", "a/.git/x"]:
        with pytest.raises(ValueError):
            runner.upload("L1", {name: b"x"})
    runner.upload("L1", {"sqlglot/parser.py": b"x"})


def test_authorization_binds_offline_worker_and_subcap(monkeypatch):
    monkeypatch.setenv("RL_E2B_TEMPLATE", "tpl")
    spec, caps, lifetimes = from_authorization(CONFIG)
    assert caps["infrastructure"] == 2_000_000
    assert caps["coding_model"] == 23_000_000 and caps["proposal"] == 0
    assert lifetimes["coding"] == 660 and spec.vcpu == 2
    monkeypatch.delenv("RL_E2B_TEMPLATE")
    spec, _, _ = from_authorization(CONFIG)
    with pytest.raises(Blocked, match="template"):
        E2BRunner(None, spec, api_key="key")
    with pytest.raises(Blocked, match="E2B_API_KEY"):
        monkeypatch.delenv("E2B_API_KEY", raising=False)
        E2BRunner(None, SPEC)
