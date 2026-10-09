"""RL0 live smoke: intake preparation, one autonomous repair attempt, and private scoring.

Every repository execution happens in an E2B sandbox under a durable lease. The
controller only builds inert requests, compares bounded outputs and diffs bytes.
Expected outputs and hidden inputs never enter a coding sandbox; the bug patch never
enters a coding sandbox either (the broken snapshot is uploaded as plain files).
"""

import base64
import json
import time
import uuid
from dataclasses import dataclass

from recursive_run.artifacts import digest
from recursive_run.contracts import State
from recursive_run.intake import validate_patch
from recursive_run.ledger import Blocked
from recursive_run.oracle import CallContract, compare_outputs
from recursive_run.patches import make_patch
from recursive_run.repositories import RepositoryProfile
from recursive_run.supply import changed_files

WORKER_TIMEOUT = 60
WORKER_OUTPUT = 1_048_576


@dataclass(frozen=True)
class SmokeTask:
    task_id: str
    family: str
    bug_patch: str
    problem: str
    problem_source: str
    contract: CallContract
    inputs: tuple[str, str]  # (distinguishing, regression); private


def _request(files, contract, text="", patches=(), **extra) -> bytes:
    return json.dumps(
        {
            "files": {k: base64.b64encode(v).decode() for k, v in files.items()},
            "contract": contract.model_dump() if contract else {},
            "input": text,
            "patches": [base64.b64encode(p).decode() for p in patches],
            "import_root": "",
            **extra,
        }
    ).encode()


def _begin(ledger, attempt_id, task_id, seconds, experiment, policy="smoke-v1"):
    ledger.create_attempt(
        attempt_id, task_id, "S", "smoke", time.time() + seconds, policy, experiment
    )
    ledger.transition(attempt_id, State.RUNNING, attempt_id + "-start")


def _lease(runner, attempt_id, purpose, lifetime):
    lease = f"{attempt_id}-{purpose}-{uuid.uuid4().hex[:8]}"
    status = runner.create(lease, attempt_id, purpose, lifetime)
    if status == "UNKNOWN":
        status = runner.reconcile(lease, time.time() + lifetime)
    if status != "ACTIVE" or lease not in runner.sessions or not runner.sessions[lease].get("host"):
        raise Blocked(f"{purpose} worker unavailable ({status}); not retried")
    return lease


def _call(runner, lease, request):
    result = runner.run_worker(lease, request, WORKER_TIMEOUT, WORKER_OUTPUT)
    return result.stdout if result.status == "OK" else None


def prepare(runner, ledger, profile: RepositoryProfile, reference, task: SmokeTask, lifetimes):
    """Intake in one preparation sandbox: reconstruct broken code, verify the reverse chain,
    freeze expected outputs from reference code and check that the broken code fails."""
    bug = task.bug_patch.encode()
    bug_paths = set(changed_files(task.bug_patch))
    validate_patch(bug, bug_paths)
    attempt = f"prep-{task.task_id}-{uuid.uuid4().hex[:6]}"
    _begin(ledger, attempt, task.task_id, lifetimes["preparation"] + 120, "smoke-intake")
    lease = _lease(runner, attempt, "preparation", lifetimes["preparation"])
    try:
        payload = _call(
            runner, lease, _request(reference, None, patches=[bug], return_files=sorted(bug_paths))
        )
        if payload is None:
            raise Blocked("broken reconstruction failed")
        rebuilt = {k: base64.b64decode(v) for k, v in json.loads(payload).items()}
        if set(rebuilt) != bug_paths:
            raise Blocked("reconstruction returned unexpected files")
        broken = {**reference, **rebuilt}
        reverse = make_patch(broken, reference)
        validate_patch(reverse, bug_paths)
        chain = _call(
            runner, lease, _request(reference, None, patches=[bug, reverse], hash_only=True)
        )
        restored = json.loads(chain) if chain else {}
        reverse_ok = all(restored.get(p) == digest(reference[p]) for p in bug_paths)
        expected, again, observed = [], [], []
        for text in task.inputs:
            expected.append(_call(runner, lease, _request(reference, task.contract, text)))
            again.append(_call(runner, lease, _request(reference, task.contract, text)))
            observed.append(_call(runner, lease, _request(broken, task.contract, text)))
    finally:
        runner.release(lease)
        ledger.transition(attempt, State.INTERRUPTED, attempt + "-done")
    if any(x is None for x in expected):
        raise Blocked("reference worker failed during intake")
    reference_result = compare_outputs(expected, again, task.contract)
    broken_result = compare_outputs(expected, observed, task.contract)
    receipt = {
        "task_id": task.task_id,
        "family": task.family,
        "reference_hash": digest(
            json.dumps({k: digest(v) for k, v in sorted(reference.items())}).encode()
        ),
        "broken_hash": digest(
            json.dumps({k: digest(v) for k, v in sorted(broken.items())}).encode()
        ),
        "bug_patch_hash": digest(bug),
        "reverse_patch_hash": digest(reverse),
        "reverse_chain_verified": reverse_ok,
        "reference_passed_twice": reference_result.success,
        "broken_outcomes": list(broken_result.outcomes),
        "broken_failed": "FAIL" in broken_result.outcomes or "MALFORMED" in broken_result.outcomes,
        "deterministic": reference_result.success,
        "contract_hash": task.contract.content_hash,
        "expected_hashes": [digest(x) for x in expected],
        "problem_source": task.problem_source,
        "problem_hash": digest(task.problem.encode()),
        "isolation": "e2b preparation sandbox, egress blocked",
    }
    receipt["accepted_for_smoke"] = all(
        [
            receipt["reverse_chain_verified"],
            receipt["reference_passed_twice"],
            receipt["broken_failed"],
        ]
    )
    return receipt, broken, expected


def export_patch(runner, lease, broken: dict[str, bytes], profile: RepositoryProfile):
    """Diff the sandbox's task tree against the uploaded broken snapshot."""
    listing = runner.hashes(lease)
    before = {k: digest(v) for k, v in broken.items()}
    changed = sorted(k for k in before if listing.get(k) != before[k])
    added = sorted(k for k in listing if k not in before)
    forbidden = [
        k
        for k in changed
        if k not in listing or listing[k] == "NONREGULAR" or not profile.editable(k)
    ] + [k for k in added if any(k.startswith(p) for p in profile.snapshot_prefixes)]
    after = {}
    for path in changed:
        if path in listing and listing[path] != "NONREGULAR" and profile.editable(path):
            content = runner.download(lease, path, 4_000_000)
            if digest(content) != listing[path]:
                raise Blocked("downloaded bytes differ from sandbox listing")
            after[path] = content
    patch = make_patch({k: broken[k] for k in after}, after)
    scratch = [k for k in added if k not in forbidden]
    return patch, sorted(after), forbidden, scratch


def score(
    runner, ledger, attempt, broken, patch, allowed, task: SmokeTask, expected, lifetimes, forbidden
):
    """Fresh scoring sandbox: reconstruct candidate from broken snapshot + patch, run each
    private input in its own process, compare with frozen reference outputs."""
    if not patch:
        return compare_outputs(expected, [], task.contract, forbidden_edit=bool(forbidden))
    try:
        validate_patch(patch, set(allowed))
    except ValueError:
        return compare_outputs(expected, [], task.contract, forbidden_edit=True)
    lease = _lease(runner, attempt, "scoring", lifetimes["scoring"])
    try:
        observed = [
            _call(runner, lease, _request(broken, task.contract, text, patches=[patch]))
            for text in task.inputs
        ]
    finally:
        runner.release(lease)
    return compare_outputs(expected, observed, task.contract, forbidden_edit=bool(forbidden))
