"""Live RL1 adapters: private task store, discovery evidence, E2B rollouts and the coach call.

`LiveRollouts` implements `campaign.RolloutPort` with the same flow the smoke trial used:
fresh coding sandbox with the broken snapshot, one mini-swe-agent session through the
admitted gateway, patch export, fresh scoring sandbox, private oracle. `LiveProposer`
implements `campaign.ProposerPort` with exactly one pinned model call per slot.

Hidden inputs and expected outputs stay in the task store. Discovery evidence is built by
deterministic extraction from the agent's own trace and the oracle's outcome categories;
it is checked against every hidden string and reduced to categories if any would leak.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from recursive_run.campaign import Proposal, RolloutResult
from recursive_run.contracts import State
from recursive_run.learning import Packet
from recursive_run.miniswe_bridge import (
    AdmittedEnvironment,
    AdmittedModel,
    parse_or_format_error,
    run_attempt,
)
from recursive_run.oracle import CallContract, LexerContract
from recursive_run.providers import openai as oa
from recursive_run.providers.e2b import E2BCodingPort
from recursive_run.repositories import PROFILES, RepositoryProfile
from recursive_run.smoke import AUTHORED_PROBLEMS, SmokeTask, _begin, _lease, export_patch, score
from recursive_run.supply import changed_files

EVIDENCE_VERSION = "evidence-v1"
CHECK_NAMES = ("distinguishing", "regression")
# 40 records (round 3: 24 S + 16 R) must fit the 24,000-token common section untruncated.
EVIDENCE_CHARS = 1_600


@dataclass(frozen=True)
class LiveTask:
    task: SmokeTask
    profile: RepositoryProfile
    broken: dict[str, bytes]
    expected: tuple[bytes, ...]

    def hidden(self) -> list[str]:
        """Strings that must never reach a learner-visible projection."""
        return [*self.task.inputs, *(x.decode(errors="replace") for x in self.expected)]


def repository_for(task_id: str) -> str:
    """Name of the admitted profile (key of PROFILES) a pool task comes from."""
    repository = "swesmith/" + task_id.rsplit(".", 1)[0]
    for name, profile in PROFILES.items():
        if profile.repository == repository and profile.admitted:
            return name
    raise ValueError(f"no admitted repository profile for {task_id}")


def _contract(draft: dict):
    if "class_name" in draft:
        return LexerContract(
            module=draft["module"], class_name=draft["class_name"], options=draft.get("options", {})
        )
    return CallContract(pipeline=draft["pipeline"], options=draft["options"])


class TaskStore:
    """Private task material keyed by full task id. Never handed to the proposer."""

    def __init__(self, drafts, frozen, rows, references, families):
        self.drafts, self.frozen, self.rows = drafts, frozen, rows
        self.references, self.families = references, families
        self._cache: dict[str, LiveTask] = {}

    def get(self, task_id: str) -> LiveTask:
        if task_id in self._cache:
            return self._cache[task_id]
        name = repository_for(task_id)
        profile = PROFILES[name]
        short = task_id.rsplit(".", 1)[-1]
        draft, item, row = self.drafts[short], self.frozen[task_id], self.rows[task_id]
        authored = AUTHORED_PROBLEMS.get(short)
        task = SmokeTask(
            task_id=task_id,
            family=self.families.get(task_id) or changed_files(row["patch"])[0],
            bug_patch=row["patch"],
            problem=authored or row["problem_statement"],
            problem_source="authored" if authored else "dataset problem_statement",
            contract=_contract(draft),
            inputs=(draft["distinguishing"], draft["regression"]),
        )
        reference = self.references[name]
        broken = {**reference, **{k: v.encode() for k, v in item["broken_files"].items()}}
        live = LiveTask(task, profile, broken, tuple(x.encode() for x in item["expected"]))
        self._cache[task_id] = live
        return live

    @classmethod
    def load(cls, pool: dict, runtime: Path, shards: Path, sources: dict[str, Path]):
        """Read the private files written by smoke and curation intake."""
        import glob

        import pyarrow.parquet as pq

        from recursive_run.inventory import snapshot_files

        drafts, frozen = {}, {}
        for name in (
            "smoke/inputs.json",
            "curation/inputs-sqlglot.json",
            "curation/inputs-pygments.json",
        ):
            drafts.update(json.loads((runtime / name).read_text()))
        for name in (
            "smoke/frozen.json",
            "curation/frozen-sqlglot.json",
            "curation/frozen-pygments.json",
        ):
            frozen.update(json.loads((runtime / name).read_text()))
        wanted = {t["task_id"] for t in pool["tasks"]}
        repositories = sorted({"swesmith/" + t.rsplit(".", 1)[0] for t in wanted})
        rows = {}
        for path in sorted(glob.glob(str(shards / "*.parquet"))):
            table = pq.read_table(
                path,
                columns=["instance_id", "repo", "patch", "problem_statement"],
                filters=[("repo", "in", repositories)],
            )
            for row in table.to_pylist():
                if row["instance_id"] in wanted:
                    rows[row["instance_id"]] = row
        missing = wanted - rows.keys()
        if missing or wanted - frozen.keys():
            raise ValueError(f"private task material incomplete: {sorted(missing)[:3]}")
        references = {}
        for task_id in wanted:
            name = repository_for(task_id)
            if name not in references:
                profile = PROFILES[name]
                references[name] = snapshot_files(sources[name], profile.reference, profile)
        families = {t["task_id"]: t["family"] for t in pool["tasks"]}
        return cls(drafts, frozen, rows, references, families)


# ---- learner-visible discovery evidence ----------------------------------------------------


def _commands(trace: dict, limit: int = 8) -> list[tuple[str, str]]:
    """(command, following observation) pairs from the agent's own trace, last `limit`."""
    messages = trace.get("messages", []) if isinstance(trace, dict) else []
    pairs = []
    for i, message in enumerate(messages):
        actions = (message.get("extra") or {}).get("actions") or []
        if message.get("role") != "assistant" or not actions:
            continue
        following = messages[i + 1] if i + 1 < len(messages) else {}
        observation = following.get("content", "") if following.get("role") == "user" else ""
        for action in actions:
            pairs.append((str(action.get("command", "")), str(observation)))
    return pairs[-limit:]


def extract_evidence(live: LiveTask, record: dict) -> str:
    """Deterministic, bounded discovery feedback for one rollout (EVIDENCE_VERSION)."""
    oracle = record.get("oracle") or {}
    outcomes = list(oracle.get("outcomes") or [])
    checks = ", ".join(
        f"{name}={outcomes[i] if i < len(outcomes) else 'NOT_RUN'}"
        for i, name in enumerate(CHECK_NAMES)
    )
    header = [
        f"task: {live.task.task_id}",
        f"verified: {'PASSED' if record.get('success') else 'FAILED'} ({record.get('state')})",
        f"hidden checks: {checks}",
        f"forbidden edit: {bool(oracle.get('forbidden_edit') or record.get('forbidden'))}",
    ]
    detail = [
        f"agent exit: {record.get('exit_status') or 'none'}; model steps: {record.get('steps')}",
        f"edited files: {', '.join(record.get('edited') or []) or 'none'}",
    ]
    if record.get("error"):
        detail.append(f"rollout error: {record['error'][:200]}")
    detail += ["problem statement:", live.task.problem.strip()[:500]]
    patch = (record.get("patch") or "").strip()
    detail += ["patch:", patch[:700] or "(no edit)"]
    for command, observation in _commands(record.get("trace") or {}, limit=5):
        detail.append(f"$ {command[:160]}")
        if observation:
            detail.append(observation.strip()[:160])
    full = "\n".join(header + detail)[:EVIDENCE_CHARS]
    if leaks(full, live.hidden()):
        return "\n".join(header + ["detail withheld: it overlapped protected checks"])
    return full


def leaks(text: str, hidden: list[str]) -> bool:
    """Canary test: any hidden string, or any substantial line of one, appears verbatim."""
    for secret in hidden:
        secret = secret.strip()
        if len(secret) >= 12 and secret in text:
            return True
        for line in secret.splitlines():
            line = line.strip()
            if len(line) >= 24 and line in text:
                return True
    return False


# ---- rollout port ----------------------------------------------------------------------------


class LiveRollouts:
    def __init__(
        self,
        ledger,
        runner,
        gateway,
        manifest,
        store: TaskStore,
        templates: dict,
        lifetimes: dict,
        config_root: Path,
        policy: str = "rl1-v1",
        max_format_errors: int = 3,
    ):
        self.ledger, self.runner, self.gateway, self.manifest = ledger, runner, gateway, manifest
        self.store, self.templates, self.lifetimes = store, templates, lifetimes
        self.config_root, self.policy = config_root, policy
        self.max_format_errors = max_format_errors

    def run(self, key, task_id, skill: bytes, arm, phase, replicate) -> RolloutResult:
        live = self.store.get(task_id)
        operation = "final" if phase == "final" else "learning"
        attempt = f"{phase}-{arm}-{key[:20]}"
        # A second call for the same key means the controller crashed mid-rollout; creating
        # the attempt again fails, so the campaign records it as interrupted (never re-run).
        _begin(
            self.ledger,
            attempt,
            task_id,
            self.lifetimes["coding"] + self.lifetimes["scoring"] + 300,
            self.manifest.experiment_id,
            self.policy,
            phase,
            arm,
        )
        record: dict = {}
        coding, patch, allowed, forbidden = None, b"", [], []
        try:
            coding = _lease(self.runner, attempt, "coding", self.lifetimes["coding"])
            self.runner.stage(coding, live.broken)
            model = AdmittedModel(
                self.ledger,
                self.gateway,
                attempt,
                self.policy,
                0,
                0,
                oa.MAXIMUM_CALL_CASH,
                operation,
                oa.encode_request,
                parse_or_format_error,
            )
            environment = AdmittedEnvironment(
                self.ledger, E2BCodingPort(self.runner, coding), attempt, self.policy, 0, 0, 0
            )
            outcome = run_attempt(
                self.manifest,
                operation,
                model,
                environment,
                live.task.problem,
                skill.decode(),
                self.templates,
                self.config_root,
                max_format_errors=self.max_format_errors,
            )
            record["exit_status"] = outcome["claim"].get("exit_status")
            record["trace"] = outcome["trace"]
            patch, allowed, forbidden, _ = export_patch(
                self.runner, coding, live.broken, live.profile
            )
            record.update(patch=patch.decode(errors="replace"), edited=allowed, forbidden=forbidden)
        except Exception as error:  # the attempt still gets scored on whatever it produced
            record["error"] = f"{type(error).__name__}: {error}"[:300]
            patch, allowed, forbidden = b"", [], []
        finally:
            if coding:
                self.runner.release(coding)
        if self.ledger.attempt(attempt)["state"] == State.RUNNING.value:
            self.ledger.transition(attempt, State.EVALUATING, attempt + "-evaluate")
            result = score(
                self.runner,
                self.ledger,
                attempt,
                live.broken,
                patch,
                allowed,
                live.task,
                list(live.expected),
                self.lifetimes,
                forbidden,
            )
            record["oracle"] = result.model_dump()
            self.ledger.transition(
                attempt,
                State.SUCCEEDED if result.success else State.FAILED,
                attempt + "-terminal",
                oracle=result,
            )
        row = self.ledger.attempt(attempt)
        record.update(state=row["state"], steps=row["steps"])
        record["success"] = row["state"] == State.SUCCEEDED.value
        cost = self.ledger.db.execute(
            "SELECT COALESCE(SUM(COALESCE(actual, maximum)), 0) FROM obligations WHERE attempt=?",
            (attempt,),
        ).fetchone()[0]
        return RolloutResult(
            key,
            task_id,
            arm,
            phase,
            hashlib.sha256(skill).hexdigest(),
            replicate,
            record["success"],
            int(cost),
            record["state"],
            evidence=extract_evidence(live, record) if phase == "discovery" else "",
        )


# ---- proposer port ---------------------------------------------------------------------------

PROPOSER_SYSTEM = """You improve a skill file for an autonomous coding agent.

The agent repairs one bug per task in a Python repository (sqlglot or Pygments) inside a
sandbox with shell commands only, at most 20 model steps. Its fixed system prompt cannot
change; the skill file below is the only text you can change. It is given to the agent at
the start of every task. Hidden checks decide success; the agent never sees them.

You get the current skill (PARENT), discovery feedback from earlier attempts (EVIDENCE),
and, when present, your own earlier proposals with their aggregate tryout results (HISTORY).
Diagnose recurring failure patterns, then write one revised skill that addresses them.
Keep it general: never mention specific task ids, files or bugs from the evidence.

Reply with exactly one JSON object and nothing else:
{"hypothesis": "<= 80 words: what you change and why", "skill": "<complete revised skill file>"}
The skill must stay under 1,200 tokens (about 4,000 characters)."""


def proposer_messages(packet: Packet) -> list[dict]:
    body = "\n\n".join(
        [
            "PARENT\n" + packet.parent.decode("utf-8"),
            "EVIDENCE\n" + packet.common.decode("utf-8"),
            "HISTORY\n" + packet.history.decode("utf-8"),
        ]
    )
    return [{"role": "system", "content": PROPOSER_SYSTEM}, {"role": "user", "content": body}]


@dataclass(frozen=True)
class _Reservation:
    maximum_cost: int


def parse_proposal(output: bytes) -> tuple[bytes | None, str, str | None]:
    try:
        envelope = json.loads(output)
        if envelope["extra"]["finish_reason"] != "stop":
            return None, "", "truncated reply"
        data = json.loads(envelope["content"])
    except (ValueError, KeyError, TypeError) as error:
        return None, "", f"malformed reply: {type(error).__name__}"
    if (
        type(data) is not dict
        or set(data) != {"hypothesis", "skill"}
        or not all(type(v) is str for v in data.values())
    ):
        return None, "", "reply must be exactly {hypothesis, skill} strings"
    return data["skill"].encode(), data["hypothesis"][:2000], None


class LiveProposer:
    """One call per slot; provider exceptions propagate so the slot stays consumed and
    its reservation stays until reconciled (`Campaign._propose`)."""

    def __init__(self, provider, maximum_cash: int):
        if maximum_cash < oa.MAXIMUM_CALL_CASH:
            raise ValueError("proposal reservation below the per-call bound")
        self.provider, self.maximum_cash = provider, maximum_cash

    def propose(self, campaign, round_number, arm, packet: Packet) -> Proposal:
        request = oa.encode_request(proposer_messages(packet))
        result = self.provider.generate(_Reservation(self.maximum_cash), request)
        content, hypothesis, failure = parse_proposal(result.output)
        return Proposal(
            content, hypothesis, result.actual_cash, result.provider_operation_id, failure
        )


def token_counter():
    import tiktoken

    encoder = tiktoken.encoding_for_model(oa.MODEL)
    return lambda data: len(encoder.encode(data.decode("utf-8"), disallowed_special=()))


# ---- gates and plan --------------------------------------------------------------------------

LIVE_PACKING = dict(
    common_tokens=24_000,
    parent_tokens=1_200,
    history_tokens=4_000,
    input_tokens=30_000,  # plus the fixed proposer instructions, under the 32,768 request bound
    extraction_version=EVIDENCE_VERSION,
    tokenizer_id=f"tiktoken:{oa.MODEL}",
)


def learning_gates(decisions: dict, pool: dict, partition: dict) -> list[str]:
    """Everything that must hold before a live learning rollout may start. Empty means go."""
    gates = []
    if decisions.get("learning_authorized") is not True:
        gates.append("learning not authorized (configs/rl1-decisions.json learning_authorized)")
    storage = decisions.get("durable_storage") or {}
    if storage.get("verified") is not True or not storage.get("receipt"):
        gates.append("durable controller storage not verified (no smoke waiver for learning)")
    unreviewed = [t["task_id"] for t in pool["tasks"] if t.get("human_reviewed") is not True]
    if unreviewed:
        gates.append(f"{len(unreviewed)} of {len(pool['tasks'])} pool tasks lack human review")
    if str(partition.get("status", "")).startswith("PROVISIONAL"):
        gates.append("partition is provisional (confirm or redraw after the review)")
    spec = partition.get("spec", {})
    assigned = [t for name in ("discovery", "selection", "final") for t in spec.get(name, [])]
    in_pool = {t["task_id"] for t in pool["tasks"]}
    if set(assigned) - in_pool:
        gates.append("partition assigns tasks outside the pool")
    return gates


def plan_from_partition(campaign: str, partition: dict, pool: dict, reuse_context: dict):
    from recursive_run.campaign import CampaignPlan
    from recursive_run.learning import PackingPolicy

    spec = partition["spec"]
    return CampaignPlan(
        campaign=campaign,
        discovery=tuple(spec["discovery"]),
        selection=tuple(spec["selection"]),
        final=tuple(spec["final"]),
        families={t["task_id"]: t["family"] for t in pool["tasks"]},
        packing=PackingPolicy(**LIVE_PACKING),
        reuse_context=reuse_context,
    )
