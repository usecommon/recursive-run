"""Offline Docker execution, deliberately ineligible as a live provider-expiry adapter.

No socket or host/controller mounts enter the worker. Inputs use a validated regular-file
map rather than an archive. All imported repository code runs in the container.
Local timeout cleanup is useful offline, but does NOT satisfy provider-enforced expiry.
"""

import base64
import json
import os
import re
import selectors
import subprocess
import time
import uuid
from dataclasses import dataclass

from recursive_run.intake import regular_path, validate_patch
from recursive_run.oracle import Contract
from recursive_run.contracts import State

# Reviewed host string, executed only by isolated Python. No candidate imports on the host.
WORKER_CODE = r"""
import base64, hashlib, importlib, json, os, pathlib, re, shutil, sys, tempfile
request = json.loads(sys.stdin.buffer.read(16777217))
# A fresh directory per process, so sequential checks never share a snapshot.
root = pathlib.Path(tempfile.mkdtemp(prefix='task-', dir='/tmp'))
if request.get('snapshot_dir'):
    # A read-only snapshot staged once per worker; copied, never modified in place.
    source = pathlib.Path(request['snapshot_dir'])
    if not str(source).startswith('/home/user/rl/snapshots/') or '..' in source.parts:
        raise SystemExit('unreviewed snapshot location')
    shutil.copytree(source, root, dirs_exist_ok=True, symlinks=False)
for name, encoded in request['files'].items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(encoded, validate=True))
for encoded_patch in request.get('patches', []):
    lines = base64.b64decode(encoded_patch, validate=True).decode('utf-8').splitlines(keepends=True)
    i = 0
    while i < len(lines):
        header = re.fullmatch(r'diff --git a/(\S+) b/(\S+)\n?', lines[i]); i += 1
        name = header[1]
        if lines[i].startswith('index '): i += 1
        i += 2
        original = (root / name).read_bytes().decode('utf-8').splitlines(keepends=True)
        result = []; cursor = 0
        while i < len(lines) and not lines[i].startswith('diff --git '):
            hunk = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[i]); i += 1
            start = int(hunk[1]) - 1 if int(hunk[2] or 1) else int(hunk[1])
            if start < cursor or start > len(original): raise ValueError('invalid hunk range')
            result.extend(original[cursor:start]); cursor = start
            while i < len(lines) and not lines[i].startswith(('@@ ', 'diff --git ')):
                line = lines[i]; i += 1
                if line.startswith('\\ No newline'): continue
                prefix, text = line[0], line[1:]
                if i < len(lines) and lines[i].startswith('\\ No newline'):
                    text = text.removesuffix('\n')
                if prefix in ' -':
                    if cursor >= len(original) or original[cursor] != text:
                        raise ValueError('preimage mismatch')
                    cursor += 1
                if prefix in ' +': result.append(text)
        result.extend(original[cursor:])
        (root / name).write_bytes(''.join(result).encode('utf-8'))
if request.get('return_files'):
    # Intake only: reconstructed bytes of the named files, as inert base64.
    payload = json.dumps({name: base64.b64encode((root / name).read_bytes()).decode()
                          for name in request['return_files']}, sort_keys=True).encode()
    sys.stdout.buffer.write(payload)
    sys.exit(0)
if request.get('hash_only'):
    payload = json.dumps({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in request['files']}, sort_keys=True).encode()
    sys.stdout.buffer.write(payload)
    sys.exit(0)
os.chdir(root)
sys.path.insert(0, str(root / request.get('import_root', '')))
contract = request['contract']
if 'pipeline' in contract:
    name, options, text = contract['pipeline'], contract['options'], request['input']
    try:
        if name == 'sqlglot.transpile':
            import sqlglot
            result = sqlglot.transpile(text, **options)
        elif name == 'sqlglot.optimize':
            import sqlglot
            from sqlglot.optimizer import optimize
            dialect = options.get('dialect')
            extra = {'isolate_tables': True} if options.get('isolate_tables') else {}
            tree = optimize(sqlglot.parse_one(text, read=dialect), schema=options.get('schema'), dialect=dialect, **extra)
            result = [tree.sql(dialect=dialect, pretty=options.get('pretty', False))]
        elif name == 'sqlglot.qualify':
            import sqlglot
            from sqlglot.optimizer.qualify import qualify
            dialect = options.get('dialect')
            tree = qualify(sqlglot.parse_one(text, read=dialect), schema=options.get('schema'), dialect=dialect, isolate_tables=bool(options.get('isolate_tables')))
            result = [tree.sql(dialect=dialect, pretty=options.get('pretty', False))]
        elif name == 'mistune.html':
            import mistune
            render = mistune.create_markdown(escape=options.get('escape', True), hard_wrap=options.get('hard_wrap', False), plugins=list(options.get('plugins', ())))
            result = [render(text)]
        else:
            raise SystemExit(3)
        if type(result) is not list or not all(type(item) is str for item in result):
            raise TypeError('pipeline returned non-string items')
        value = {'ok': result}
    except Exception as error:
        value = {'error': type(error).__name__[:200]}
    payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > contract['maximum_output_bytes']:
        raise RuntimeError('output limit exceeded')
    sys.stdout.buffer.write(payload)
    sys.exit(0)
try:
    # Code under test may raise; report only the exception class, as inert data. The
    # evaluator's token parser rejects it (MALFORMED), so a crash fails the check and
    # WORKER_ERROR stays reserved for worker or infrastructure failures.
    lexer_class = getattr(importlib.import_module(contract['module']), contract['class_name'])
    lexer = lexer_class(**contract['options'])
    tokens = []
    for offset, kind, text in lexer.get_tokens_unprocessed(request['input']):
        tokens.append([offset, str(kind), text])
        if len(tokens) > contract['maximum_tokens']:
            raise OverflowError('token limit exceeded')
    value = tokens
except Exception as error:
    value = {'error': type(error).__name__[:200]}
payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
if len(payload) > contract['maximum_output_bytes']:
    raise RuntimeError('output limit exceeded')
sys.stdout.buffer.write(payload)
"""


@dataclass(frozen=True)
class WorkerOutput:
    name: str
    status: str
    output: bytes
    stderr: bytes
    removed: bool
    seconds: float


def docker_command(*arguments: str) -> list[str]:
    return ["docker", "--host=unix:///var/run/docker.sock", *arguments]


def docker_environment():
    # Keep registry settings and transport trust, while selecting the managed local daemon.
    return {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        }
    }


class OfflineDockerRunner:
    def __init__(self, image_digest: str, timeout: float = 20, maximum_files_bytes=12_000_000):
        if not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}", image_digest):
            raise ValueError("worker image must be pinned by digest")
        if not 0 < timeout <= 60:
            raise ValueError("offline timeout must be 0–60 seconds")
        self.image = image_digest
        self.timeout = timeout
        self.maximum_files_bytes = maximum_files_bytes

    def lex(
        self,
        files: dict[str, bytes],
        contract: Contract,
        text: str,
        *,
        ledger,
        attempt_id: str,
        patches: tuple[bytes, ...] = (),
        allowed_paths: set[str] | None = None,
        hash_only: bool = False,
        import_root: str = "",
    ) -> WorkerOutput:
        if len(text.encode("utf-8")) > contract.maximum_input_bytes:
            raise ValueError("input exceeds contract")
        if import_root:
            regular_path(import_root)
        size = 0
        encoded = {}
        for name, content in files.items():
            regular_path(name)
            if name.startswith(".") or "/.git/" in "/" + name + "/":
                raise ValueError("hidden/controller metadata prohibited")
            size += len(content)
            if size > self.maximum_files_bytes:
                raise ValueError("snapshot exceeds limit")
            encoded[name] = base64.b64encode(content).decode("ascii")
        for patch in patches:
            validate_patch(patch, allowed_paths or set())
        request = json.dumps(
            {
                "files": encoded,
                "contract": contract.model_dump(),
                "input": text,
                "patches": [base64.b64encode(p).decode() for p in patches],
                "hash_only": hash_only,
                "import_root": import_root,
            }
        ).encode()
        if len(request) > 16_777_216:
            raise ValueError("request exceeds serialization limit")
        name = "rl-offline-" + uuid.uuid4().hex
        command = docker_command(
            "run",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "--env=HTTP_PROXY=",
            "--env=HTTPS_PROXY=",
            "--env=ALL_PROXY=",
            "--env=NO_PROXY=",
            "--env=http_proxy=",
            "--env=https_proxy=",
            "--env=all_proxy=",
            "--env=no_proxy=",
            "-i",
            self.image,
            "python",
            "-I",
            "-c",
            WORKER_CODE,
        )
        ledger.create_lease(
            name, attempt_id, "preparation", self.image, time.time() + self.timeout + 20, 0
        )
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=docker_environment(),
        )
        stdout, stderr = bytearray(), bytearray()
        status = "WORKER_ERROR"
        removed = False
        try:
            # Nonblocking simultaneous input/output prevents pipe deadlocks and bounds output.
            selector = selectors.DefaultSelector()
            for pipe, tag in [
                (process.stdin, "in"),
                (process.stdout, "out"),
                (process.stderr, "err"),
            ]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(
                    pipe, selectors.EVENT_WRITE if tag == "in" else selectors.EVENT_READ, tag
                )
            position = 0
            while selector.get_map():
                if time.monotonic() - started > self.timeout:
                    status = "TIMEOUT"
                    break
                for key, _ in selector.select(timeout=0.1):
                    if key.data == "in":
                        try:
                            written = os.write(key.fd, request[position : position + 65536])
                            position += written
                        except BrokenPipeError:
                            position = len(request)
                        if position == len(request):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        output = stdout if key.data == "out" else stderr
                        output.extend(chunk)
                        limit = contract.maximum_output_bytes if key.data == "out" else 65536
                        if len(output) > limit:
                            status = "MALFORMED"
                            raise ValueError("bounded worker output exceeded")
            else:
                remaining = max(0.01, self.timeout - (time.monotonic() - started))
                status = "OK" if process.wait(timeout=remaining) == 0 else "WORKER_ERROR"
        except (ValueError, subprocess.TimeoutExpired):
            if status != "MALFORMED":
                status = "TIMEOUT"
        finally:
            # Remove ONLY our unique worker; verify absence independently of rm acknowledgement.
            subprocess.run(
                docker_command("rm", "-f", name),
                capture_output=True,
                timeout=10,
                env=docker_environment(),
            )
            check = subprocess.run(
                docker_command("container", "inspect", name),
                capture_output=True,
                timeout=10,
                env=docker_environment(),
            )
            removed = check.returncode == 1 and b"No such" in check.stderr
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
            if removed:
                ledger.observe_lease(name, "ABSENT", name, name + "-absence")
                ledger.settle("lease:" + name, 0, name, name + "-offline-receipt")
            else:
                status = "UNCONFIRMED_RELEASE"
            if ledger.attempt(attempt_id)["state"] == "RUNNING":
                ledger.transition(attempt_id, State.INTERRUPTED, name + "-finished")
        return WorkerOutput(
            name,
            status,
            bytes(stdout[: contract.maximum_output_bytes]),
            bytes(stderr[:65536]),
            removed,
            time.monotonic() - started,
        )
