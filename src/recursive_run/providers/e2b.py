"""E2B sandbox runner over plain HTTPS, without the E2B SDK and without implicit retries.

The control plane is E2B's REST API; commands and files go to the sandbox's envd
service (REST for files, Connect protocol for processes, JSON codec over HTTP/1.1).
Neither path needs gRPC trailers, so both pass the managed HTTPS proxy.

Every create is preceded by a durable lease with an absolute expiry. The sandbox is
created with the same lifetime, auto-pause off and internet access off, so E2B kills it
even if this controller disappears. A create whose outcome is unknown is reconciled by
the lease id stored in sandbox metadata, never retried blindly. Absence is concluded only
from a provider 404, or from a lease that is unlisted after its provider-enforced expiry.
"""

import base64
import json
import math
import os
import ssl
import struct
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote, urlencode

import httpx

from recursive_run.gateway import DispatchCapabilities
from recursive_run.intake import regular_path
from recursive_run.ledger import Blocked
from recursive_run.workers import WORKER_CODE

API = "https://api.e2b.app"

# Reviewed sandbox-side helpers, run with `python3 -I`. They write or hash regular files
# under the task directory only and print inert JSON.
UNPACK_CODE = r"""
import base64, json, pathlib, sys
root = pathlib.Path(sys.argv[2])
for name, encoded in json.loads(pathlib.Path(sys.argv[1]).read_bytes()).items():
    parts = name.split('/')
    if name.startswith('/') or any(p in ('', '.', '..') for p in parts):
        raise SystemExit('unsafe path')
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(encoded, validate=True))
pathlib.Path(sys.argv[1]).unlink()
"""
HASH_CODE = r"""
import hashlib, json, os, sys
root, listing = sys.argv[1], {}
for base, dirs, files in os.walk(root, followlinks=False):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for f in files:
        path = os.path.join(base, f)
        name = os.path.relpath(path, root)
        if os.path.islink(path) or not os.path.isfile(path):
            listing[name] = 'NONREGULAR'
        else:
            with open(path, 'rb') as h:
                listing[name] = hashlib.sha256(h.read()).hexdigest()
        if len(listing) > 20000:
            raise SystemExit('too many files')
print(json.dumps(listing, sort_keys=True))
"""
ENVD_PORT = 49983
WORKDIR = "/home/user/task"
CONTROL = "/home/user/rl"  # reviewed worker code and inert requests, outside the task tree


@dataclass(frozen=True)
class SandboxSpec:
    template_id: str
    vcpu: int
    memory_gib: int
    usd_per_vcpu_second: Decimal
    usd_per_gib_second: Decimal

    def maximum_cost(self, seconds: float) -> int:
        """Upper bound in integer micro-USD, rounded up."""
        rate = self.vcpu * self.usd_per_vcpu_second + self.memory_gib * self.usd_per_gib_second
        return math.ceil(Decimal(str(seconds)) * rate * 1_000_000)


@dataclass(frozen=True)
class CommandResult:
    status: str  # OK, NONZERO, TIMEOUT, MALFORMED, TRANSPORT_ERROR
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    seconds: float


def from_authorization(path) -> tuple[SandboxSpec, dict[str, int], dict[str, int]]:
    """Read the smoke authorization: sandbox spec, ledger category caps and lifetimes."""
    data = json.loads(open(path).read())
    if data.get("worker_provider") != "E2B" or data.get("e2b_internet_access") is not False:
        raise Blocked("authorization does not bind an offline E2B worker")
    if data.get("e2b_auto_pause") is not False:
        raise Blocked("auto-pause would let an expired sandbox persist")
    template = os.environ.get("RL_E2B_TEMPLATE", "")
    spec = SandboxSpec(
        template_id=template,
        vcpu=int(data["e2b_sandbox_vcpu"]),
        memory_gib=int(data["e2b_sandbox_memory_gib"]),
        usd_per_vcpu_second=Decimal(data["e2b_usd_per_second"]["vcpu"]),
        usd_per_gib_second=Decimal(data["e2b_usd_per_second"]["GiB_memory"]),
    )
    caps = {
        "coding_model": data["maximum_external_cash_micro_usd"]
        - data["maximum_worker_cash_micro_usd"],
        "proposal": 0,
        "infrastructure": data["maximum_worker_cash_micro_usd"],
    }
    lifetimes = {
        "coding": int(data["e2b_coding_lifetime_seconds"]),
        "scoring": int(data["e2b_scoring_lifetime_seconds"]),
        "preparation": int(data["e2b_scoring_lifetime_seconds"]),
    }
    return spec, caps, lifetimes


def _client() -> httpx.Client:
    # System trust store carries the managed proxy CA; the proxy comes from the environment.
    context = ssl.create_default_context()
    return httpx.Client(verify=context, trust_env=True, http2=False, follow_redirects=False)


def _frames(chunks, limit):
    """Yield (flags, message) from Connect envelopes: 1 flag byte, 4-byte length, JSON."""
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        while len(buffer) >= 5:
            flags, size = struct.unpack(">BI", buffer[:5])
            if size > limit:
                raise OverflowError("envelope exceeds bound")
            if len(buffer) < 5 + size:
                break
            yield flags, json.loads(buffer[5 : 5 + size])
            buffer = buffer[5 + size :]


def _envelope(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode()
    return struct.pack(">BI", 0, len(body)) + body


class E2BRunner:
    capabilities = DispatchCapabilities(
        lookup=True,
        idempotent_replay=False,
        sdk_retries_disabled=True,
        protocol_hash="e2b-rest-v2+envd-connect-json-v1",
    )

    def __init__(
        self,
        ledger,
        spec: SandboxSpec,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        request_timeout: float = 30,
        clock=time.time,
    ):
        if not spec.template_id:
            raise Blocked("pinned E2B template is unbound (RL_E2B_TEMPLATE)")
        self.api_key = api_key or os.environ.get("E2B_API_KEY")
        if not self.api_key:
            raise Blocked("E2B_API_KEY binding missing")
        self.ledger, self.spec = ledger, spec
        self.http = client or _client()
        self.timeout = request_timeout
        self.clock = clock
        self.sessions: dict[str, dict] = {}  # lease -> sandbox id, envd host, token (memory only)

    # ---- control plane -------------------------------------------------------------

    def _api(self, method, path, **kwargs) -> httpx.Response:
        headers = {"X-API-Key": self.api_key, **kwargs.pop("headers", {})}
        return self.http.request(
            method, API + path, headers=headers, timeout=self.timeout, **kwargs
        )

    def create(self, lease_id: str, attempt_id: str, purpose: str, lifetime: int) -> str:
        """Persist the lease, then create one sandbox. Returns ACTIVE, ABSENT or UNKNOWN."""
        now = self.clock()
        expiry = now + lifetime
        self.ledger.create_lease(
            lease_id,
            attempt_id,
            purpose,
            self.spec.template_id,
            expiry,
            self.spec.maximum_cost(lifetime),
            now=now,
        )
        body = {
            "templateID": self.spec.template_id,
            "timeout": lifetime,
            "autoPause": False,
            # Both forms: a 2026-10-08 live probe reached the internet with the flag alone.
            "allow_internet_access": False,
            "network": {"denyOut": ["0.0.0.0/0"], "allowOut": []},
            "metadata": {"rl_lease": lease_id, "rl_attempt": attempt_id},
        }
        try:
            response = self._api("POST", "/v2/sandboxes", json=body)
        except httpx.HTTPError:
            return "UNKNOWN"  # Request may have reached E2B; reconcile, never resend.
        if response.status_code in {400, 401, 403, 404, 429}:
            # E2B rejected the request before creating anything.
            self.ledger.observe_lease(lease_id, "ABSENT", None, lease_id + "-rejected")
            self.ledger.settle("lease:" + lease_id, 0, lease_id, lease_id + "-nonexecution")
            return "ABSENT"
        if response.status_code != 201:
            return "UNKNOWN"
        sandbox = response.json()
        self._remember(lease_id, sandbox)
        self.ledger.observe_lease(lease_id, "ACTIVE", sandbox["sandboxID"], lease_id + "-created")
        self._verify_resources(lease_id)
        return "ACTIVE"

    def _remember(self, lease_id, sandbox):
        self.sessions[lease_id] = {
            "id": sandbox["sandboxID"],
            "host": f"{ENVD_PORT}-{sandbox['sandboxID']}.{sandbox.get('domain') or 'e2b.app'}",
            "token": sandbox.get("envdAccessToken"),
            "started": self.clock(),
        }

    def _verify_resources(self, lease_id):
        detail = self._api("GET", f"/sandboxes/{self.sessions[lease_id]['id']}")
        if detail.status_code != 200:
            return
        info = detail.json()
        self.sessions[lease_id]["started_at"] = info.get("startedAt")
        if (
            info.get("cpuCount", 0) > self.spec.vcpu
            or info.get("memoryMB", 0) > self.spec.memory_gib * 1024
        ):
            self.release(lease_id)
            raise Blocked("template resources exceed the priced sandbox size")

    def reconcile(self, lease_id: str, expiry: float) -> str:
        """Find a sandbox by lease metadata after an uncertain create or a restart."""
        query = urlencode({"metadata": f"rl_lease={quote(lease_id)}", "state": "running,paused"})
        response = self._api("GET", "/v2/sandboxes?" + query)
        if response.status_code != 200:
            return "UNKNOWN"
        found = [
            s for s in response.json() if (s.get("metadata") or {}).get("rl_lease") == lease_id
        ]
        if len(found) > 1:
            raise Blocked("duplicate sandboxes share one lease identity")
        if found:
            self.ledger.observe_lease(
                lease_id, "ACTIVE", found[0]["sandboxID"], lease_id + "-reconciled"
            )
            self.sessions.setdefault(lease_id, {"id": found[0]["sandboxID"], "token": None})
            return "ACTIVE"
        if self.clock() > expiry:
            # Unlisted after the provider-enforced lifetime: absence is established. Its
            # runtime is unknown, so the whole reserved maximum is settled.
            self.ledger.observe_lease(lease_id, "ABSENT", None, lease_id + "-expired")
            self.ledger.settle(
                "lease:" + lease_id,
                self._reserved(lease_id),
                lease_id,
                lease_id + "-expired-settled",
            )
            return "ABSENT"
        return "UNKNOWN"

    def _reserved(self, lease_id) -> int:
        row = self.ledger.db.execute(
            "SELECT maximum FROM obligations WHERE id=?", ("lease:" + lease_id,)
        ).fetchone()
        return row[0]

    def release(self, lease_id: str) -> bool:
        """Kill the sandbox, then confirm absence independently of the kill reply."""
        session = self.sessions.get(lease_id)
        if session is None:
            return False
        try:
            self._api("DELETE", f"/sandboxes/{session['id']}")
            check = self._api("GET", f"/sandboxes/{session['id']}")
        except httpx.HTTPError:
            self.ledger.observe_lease(
                lease_id, "RELEASE_PENDING", session["id"], lease_id + "-release-pending"
            )
            return False
        if check.status_code != 404:
            self.ledger.observe_lease(
                lease_id, "RELEASE_PENDING", session["id"], lease_id + "-release-pending"
            )
            return False
        seconds = max(1.0, self.clock() - session.get("started", self.clock()))
        self.ledger.observe_lease(lease_id, "ABSENT", session["id"], lease_id + "-absent")
        # No per-sandbox billing receipt exists; settle the priced upper bound for the
        # observed lifetime (from before create until verified absence).
        self.ledger.settle(
            "lease:" + lease_id,
            min(self.spec.maximum_cost(seconds), self._reserved(lease_id)),
            session["id"],
            lease_id + "-settled",
        )
        self.sessions.pop(lease_id, None)
        return True

    # ---- data plane (envd) ---------------------------------------------------------

    def _envd_headers(self, session, content_type=None):
        headers = {}
        if session.get("token"):
            headers["X-Access-Token"] = session["token"]
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _put(self, session, path: str, content: bytes):
        response = self.http.post(
            f"https://{session['host']}/files",
            params={"path": path},
            headers=self._envd_headers(session),
            files={"file": (path.rsplit("/", 1)[-1], content)},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise Blocked(f"sandbox file upload failed with HTTP {response.status_code}")

    def upload(self, lease_id: str, files: dict[str, bytes], maximum_bytes=12_000_000):
        """Write a validated regular-file map under the task directory; no archives."""
        session = self.sessions[lease_id]
        if sum(len(v) for v in files.values()) > maximum_bytes:
            raise ValueError("snapshot exceeds limit")
        for name, content in files.items():
            regular_path(name)
            if name.startswith(".") or "/.git/" in "/" + name + "/":
                raise ValueError("hidden/controller metadata prohibited")
            self._put(session, f"{WORKDIR}/{name}", content)

    def stage(
        self, lease_id: str, files: dict[str, bytes], maximum_bytes=12_000_000, target=WORKDIR
    ):
        """Upload a validated regular-file map as one bundle and unpack it in the sandbox."""
        session = self.sessions[lease_id]
        size = 0
        for name, content in files.items():
            regular_path(name)
            if name.startswith(".") or "/.git/" in "/" + name + "/":
                raise ValueError("hidden/controller metadata prohibited")
            size += len(content)
        if size > maximum_bytes:
            raise ValueError("snapshot exceeds limit")
        bundle = json.dumps({k: base64.b64encode(v).decode() for k, v in files.items()}).encode()
        self._put(session, f"{CONTROL}/unpack.py", UNPACK_CODE.encode())
        self._put(session, f"{CONTROL}/bundle.json", bundle)
        result = self.run(
            lease_id,
            f"python3 -I {CONTROL}/unpack.py {CONTROL}/bundle.json {target}",
            timeout=120,
            maximum_output=65536,
        )
        if result.status != "OK":
            raise Blocked("snapshot staging failed in sandbox")

    def hashes(self, lease_id: str) -> dict[str, str]:
        """SHA-256 of every file under the task directory, computed in the sandbox."""
        session = self.sessions[lease_id]
        self._put(session, f"{CONTROL}/hash.py", HASH_CODE.encode())
        result = self.run(
            lease_id,
            f"python3 -I {CONTROL}/hash.py {WORKDIR}",
            timeout=120,
            maximum_output=4_000_000,
        )
        if result.status != "OK":
            raise Blocked("hash listing failed in sandbox")
        listing = json.loads(result.stdout)
        if type(listing) is not dict or not all(
            type(k) is str and type(v) is str for k, v in listing.items()
        ):
            raise ValueError("malformed hash listing")
        return listing

    def run_worker(self, lease_id: str, request: bytes, timeout: float, maximum_output: int):
        """Run the reviewed scoring worker (workers.WORKER_CODE) on one inert request."""
        session = self.sessions[lease_id]
        self._put(session, f"{CONTROL}/worker.py", WORKER_CODE.encode())
        self._put(session, f"{CONTROL}/request.json", request)
        return self.run(
            lease_id,
            f"python3 -I {CONTROL}/worker.py < {CONTROL}/request.json",
            timeout,
            maximum_output,
        )

    def download(self, lease_id: str, path: str, maximum_bytes: int) -> bytes:
        session = self.sessions[lease_id]
        with self.http.stream(
            "GET",
            f"https://{session['host']}/files",
            params={"path": f"{WORKDIR}/{path}"},
            headers=self._envd_headers(session),
            timeout=self.timeout,
        ) as response:
            if response.status_code != 200:
                raise Blocked(f"sandbox file download failed with HTTP {response.status_code}")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > maximum_bytes:
                    raise ValueError("download exceeds bound")
            return bytes(data)

    def run(self, lease_id: str, command: str, timeout: float, maximum_output: int):
        """Run one bash command in the task directory; output and wall time are bounded."""
        session = self.sessions[lease_id]
        script = f"mkdir -p {WORKDIR} && cd {WORKDIR} && {command}"
        request = {
            "process": {"cmd": "/bin/bash", "args": ["-c", script], "envs": {}, "cwd": "/"},
            "stdin": False,
        }
        headers = self._envd_headers(session, "application/connect+json")
        headers["Connect-Protocol-Version"] = "1"
        started = time.monotonic()
        stdout, stderr = bytearray(), bytearray()
        pid, exit_code, status = None, None, "MALFORMED"
        try:
            with self.http.stream(
                "POST",
                f"https://{session['host']}/process.Process/Start",
                content=_envelope(request),
                headers=headers,
                # The read timeout bounds a silent process; the loop bounds a chatty one.
                timeout=httpx.Timeout(timeout, connect=self.timeout),
            ) as response:
                if response.status_code != 200:
                    return CommandResult("TRANSPORT_ERROR", None, b"", b"", 0.0)
                for flags, frame in _frames(response.iter_bytes(), 4 * maximum_output + 1024):
                    if time.monotonic() - started > timeout:
                        status = "TIMEOUT"
                        break
                    if flags & 0x02:  # end of stream; Connect reports errors in the body
                        status = "TRANSPORT_ERROR" if frame.get("error") else status
                        break
                    event = frame.get("event", {})
                    if "start" in event:
                        pid = event["start"].get("pid")
                    elif "data" in event:
                        for key, sink in (("stdout", stdout), ("stderr", stderr)):
                            if key in event["data"]:
                                sink.extend(base64.b64decode(event["data"][key]))
                        if len(stdout) > maximum_output or len(stderr) > maximum_output:
                            status = "MALFORMED"
                            break
                    elif "end" in event:
                        exit_code = int(event["end"].get("exitCode", 0))
                        status = "OK" if exit_code == 0 else "NONZERO"
                        break
        except (OverflowError, ValueError):
            status = "MALFORMED"
        except httpx.TimeoutException:
            status = "TIMEOUT"
        except httpx.HTTPError:
            status = "TRANSPORT_ERROR"
        if status in {"TIMEOUT", "MALFORMED"} and pid is not None:
            self._kill(session, pid)
        return CommandResult(
            status,
            exit_code,
            bytes(stdout[:maximum_output]),
            bytes(stderr[:65536]),
            time.monotonic() - started,
        )

    def _kill(self, session, pid):
        try:
            self.http.post(
                f"https://{session['host']}/process.Process/SendSignal",
                json={"process": {"pid": pid}, "signal": "SIGNAL_SIGKILL"},
                headers={**self._envd_headers(session), "Connect-Protocol-Version": "1"},
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            pass  # The sandbox lifetime still bounds the process.


class E2BCodingPort:
    """Worker port for miniswe_bridge.AdmittedEnvironment: one admitted command per call."""

    def __init__(self, runner: E2BRunner, lease_id: str, command_timeout=60, maximum_output=65536):
        self.runner, self.lease_id = runner, lease_id
        self.command_timeout, self.maximum_output = command_timeout, maximum_output

    def execute(self, admitted, action) -> dict:
        if (
            admitted.attempt_id
            != self.runner.ledger.db.execute(
                "SELECT attempt FROM leases WHERE id=?", (self.lease_id,)
            ).fetchone()[0]
        ):
            raise Blocked("action admitted for a different attempt than this lease")
        result = self.runner.run(
            self.lease_id, action["command"], self.command_timeout, self.maximum_output
        )
        text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        if result.status == "TIMEOUT":
            text += f"\n[command exceeded {self.command_timeout} s and was killed]"
        elif result.status == "MALFORMED":
            text += "\n[output exceeded the bound and was truncated; command killed]"
        elif result.status == "TRANSPORT_ERROR":
            raise Blocked("worker transport failed; attempt cannot continue")
        # Worker time is charged on the lease, not per command.
        return {
            "output": text[: self.maximum_output],
            "returncode": result.exit_code if result.exit_code is not None else -1,
            "actual_cash": 0,
            "operation_id": admitted.operation_key,
        }
