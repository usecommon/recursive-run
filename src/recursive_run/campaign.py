"""RL1 learning campaign: S/B/R schedule, matched packets, selection, sealed final, report.

Live execution sits behind two ports, so the schedule logic is the same under mocks and
live runs:

- `RolloutPort.run` performs one autonomous repair attempt with a given skill and returns
  a verified outcome (the private oracle decides success, never the agent's claim).
- `ProposerPort.propose` turns one packet into one candidate skill with one model call.

The campaign never retries. A rollout that cannot complete counts as a failure for its
assigned cell. A proposal that fails or is interrupted consumes its slot. Every step has a
deterministic key stored on the controller ledger, so a restarted campaign resumes without
re-running finished work. Final outcomes stay sealed until every final cell is terminal.
"""

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from recursive_run.analysis import ReplicatedOutcome, replicated_screening_report
from recursive_run.learning import (
    REUSE_FIELDS,
    Candidate,
    Evidence,
    History,
    LearningRegistry,
    Packet,
    PackingPolicy,
    SelectionVector,
    canonical_skill,
    pack,
    stable_hash,
)


@dataclass(frozen=True)
class RolloutResult:
    key: str
    task_id: str
    arm: str
    phase: str
    skill_hash: str
    replicate: int
    success: bool
    cost: int
    state: str
    evidence: str = ""


class RolloutPort(Protocol):
    def run(
        self, key: str, task_id: str, skill: bytes, arm: str, phase: str, replicate: int
    ) -> RolloutResult: ...


@dataclass(frozen=True)
class Proposal:
    content: bytes | None
    hypothesis: str
    cost: int
    operation_id: str
    failure: str | None = None


class ProposerPort(Protocol):
    def propose(self, campaign: str, round_number: int, arm: str, packet: Packet) -> Proposal: ...


@dataclass(frozen=True)
class CampaignPlan:
    campaign: str
    discovery: tuple[str, ...]  # 24 tasks; batches are consecutive eights in this order
    selection: tuple[str, ...]  # 16 tasks
    final: tuple[str, ...]  # 40 tasks
    families: dict
    packing: PackingPolicy
    reuse_context: dict  # every REUSE_FIELDS key except "campaign"
    selection_replicates: int = 3
    final_replicates: int = 3
    proposal_cash: int = 2_000_000
    order_seed: int = 2718
    artifact_extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if (len(self.discovery), len(self.selection), len(self.final)) != (24, 16, 40):
            raise ValueError("RL1 requires 24/16/40 tasks")
        if len(set(self.discovery + self.selection + self.final)) != 80:
            raise ValueError("partitions overlap")
        if set(self.reuse_context) != REUSE_FIELDS - {"campaign"}:
            raise ValueError("reuse context must name every frozen field except campaign")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Campaign:
    def __init__(
        self,
        ledger,
        plan: CampaignPlan,
        rollouts: RolloutPort,
        proposer: ProposerPort,
        count: Callable[[bytes], int],
        skill_s: bytes,
    ):
        self.ledger, self.plan = ledger, plan
        self.rollouts, self.proposer, self.count = rollouts, proposer, count
        self.registry = LearningRegistry(ledger)
        ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS campaign_rollouts(key TEXT PRIMARY KEY, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaign_state(name TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        content, digest, tokens = canonical_skill(skill_s, count)
        self.s = {"content": content, "hash": digest, "tokens": tokens, "version": 0}
        self.context = {"campaign": plan.campaign, **plan.reuse_context}

    # ---- durable state ------------------------------------------------------------------

    def _get(self, name, default=None):
        row = self.ledger.db.execute(
            "SELECT value FROM campaign_state WHERE name=?", (name,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def _put(self, name, value):
        with self.ledger.transaction():
            self.ledger.db.execute(
                "INSERT OR REPLACE INTO campaign_state VALUES(?,?)", (name, json.dumps(value))
            )

    def _rollout(self, task_id, skill: bytes, arm, phase, replicate) -> RolloutResult:
        skill_hash = _sha(skill)
        key = stable_hash([self.plan.campaign, phase, arm, skill_hash, task_id, replicate])[:32]
        row = self.ledger.db.execute(
            "SELECT result FROM campaign_rollouts WHERE key=?", (key,)
        ).fetchone()
        if row:
            return RolloutResult(**json.loads(row[0]))
        try:
            result = self.rollouts.run(key, task_id, skill, arm, phase, replicate)
        except Exception as error:  # infrastructure or admission failure: no retry
            result = RolloutResult(
                key,
                task_id,
                arm,
                phase,
                skill_hash,
                replicate,
                False,
                0,
                f"INTERRUPTED: {type(error).__name__}: {error}"[:300],
            )
        if result.key != key or result.skill_hash != skill_hash:
            raise ValueError("rollout port returned a result for a different cell")
        with self.ledger.transaction():
            self.ledger.db.execute(
                "INSERT INTO campaign_rollouts VALUES(?,?)", (key, json.dumps(asdict(result)))
            )
        return result

    # ---- phases -------------------------------------------------------------------------

    def _batch(self, index):
        return self.plan.discovery[8 * index : 8 * index + 8]

    def _discovery(self, skill, arm, batch):
        return [self._rollout(t, skill, arm, "discovery", 0) for t in self._batch(batch)]

    def _selection_vector(self, skill: bytes, arm: str) -> SelectionVector:
        k = self.plan.selection_replicates
        results = [
            self._rollout(t, skill, arm, "selection", rep)
            for t in self.plan.selection
            for rep in range(k)
        ]
        return SelectionVector(tuple(r.success for r in results), sum(r.cost for r in results), k)

    def _candidate(self, record) -> Candidate:
        vector = SelectionVector(
            tuple(record["outcomes"]), record["cost"], self.plan.selection_replicates
        )
        return Candidate(
            record["content"].encode(), record["hash"], record["tokens"], record["version"], vector
        )

    def baseline(self):
        for batch in range(3):
            self._discovery(self.s["content"], "S", batch)
        if self._get("S") is None:
            vector = self._selection_vector(self.s["content"], "S")
            self._put(
                "S",
                {
                    "content": self.s["content"].decode(),
                    "hash": self.s["hash"],
                    "tokens": self.s["tokens"],
                    "version": 0,
                    "outcomes": list(vector.outcomes),
                    "cost": vector.total_cost,
                },
            )

    def _evidence(self, round_number) -> list[Evidence]:
        items = []
        for batch in range(round_number):
            for r in self._discovery(self.s["content"], "S", batch):
                items.append(Evidence(r.key, r.task_id, "S", r.evidence))
        ran = self._get("R_batch_skill", {})
        for batch in range(1, round_number):
            # R's traces come from the incumbent that was current when that batch ran.
            for r in self._discovery(ran[str(batch)].encode(), "R", batch):
                items.append(Evidence(r.key, r.task_id, "R", r.evidence))
        return items

    def _history(self) -> list[History]:
        return [History(**h) for h in self._get("R_history", [])]

    def _keep(self, record):
        return {k: record[k] for k in ("content", "hash", "tokens", "version", "outcomes", "cost")}

    def round(self, round_number: int):
        if self._get(f"round{round_number}") == "DONE":
            return
        incumbent = self._get("R_incumbent", self._get("S"))
        if round_number >= 2:
            ran = self._get("R_batch_skill", {})
            if str(round_number - 1) not in ran:
                ran[str(round_number - 1)] = incumbent["content"]  # frozen before the batch runs
                self._put("R_batch_skill", ran)
        evidence = self._evidence(round_number)
        new = {}
        for arm, parent in (("B", self._get("S")), ("R", incumbent)):
            history = self._history() if arm == "R" else []
            packet = pack(
                round_number,
                arm,
                evidence,
                parent["content"].encode(),
                history,
                self.plan.packing,
                self.count,
            )
            new[arm] = self._propose(round_number, arm, packet, parent)
        version = self._get("version", 0)
        for arm in ("B", "R"):  # B first, so an R alias of B's candidate finds its vector
            proposal = new[arm]
            if proposal["status"] == "REJECTED" or "outcomes" in proposal:
                continue
            stored = self.ledger.db.execute(
                "SELECT vector FROM selections WHERE reuse_key=?", (proposal["reuse_key"],)
            ).fetchone()
            if stored:  # canonical alias within this campaign: reuse, never redraw
                vector = json.loads(stored[0])
                outcomes, cost = vector["outcomes"], vector["cost"]
            else:
                selected = self._selection_vector(proposal["content"].encode(), arm)
                self.registry.store_selection(
                    proposal["reuse_key"], proposal["content_hash"], selected
                )
                outcomes, cost = list(selected.outcomes), selected.total_cost
            version += 1
            proposal.update(
                outcomes=outcomes,
                cost=cost,
                version=version,
                hash=proposal["content_hash"],
                tokens=canonical_skill(proposal["content"].encode(), self.count)[2],
            )
            self._put(f"proposal:{round_number}:{arm}", proposal)
        self._put("version", version)
        # R replaces its incumbent only with a strictly higher-ranked candidate; B keeps the
        # best of S and its independent revisions.
        promoted = "outcomes" in new["R"] and (
            self._candidate(new["R"]).rank < self._candidate(incumbent).rank
        )
        if promoted:
            self._put("R_incumbent", self._keep(new["R"]))
        best_b = self._get("B_best", self._get("S"))
        if "outcomes" in new["B"] and self._candidate(new["B"]).rank < self._candidate(best_b).rank:
            self._put("B_best", self._keep(new["B"]))
        r = new["R"]
        fields = {
            "hypothesis": r.get("hypothesis", ""),
            "promotion": "ACCEPTED" if promoted else "REJECTED",
        }
        if "outcomes" in r:
            fields.update(
                content=r["content"],
                selection_successes=sum(r["outcomes"]),
                selection_cost=r["cost"],
            )
        else:
            fields["failure"] = r.get("failure") or r["status"]
        history = self._get("R_history", [])
        history.append(
            {"round": round_number, "occurrence": round_number, "origin_arm": "R", "fields": fields}
        )
        self._put("R_history", history)
        self._put(f"round{round_number}", "DONE")

    def _propose(self, round_number, arm, packet: Packet, parent) -> dict:
        name = f"proposal:{round_number}:{arm}"
        stored = self._get(name)
        if stored is not None:
            return stored
        row = self.ledger.db.execute(
            "SELECT status FROM proposals WHERE campaign=? AND round=? AND arm=?",
            (self.plan.campaign, round_number, arm),
        ).fetchone()
        if row is None:
            self.registry.consume_slot(
                self.plan.campaign,
                round_number,
                arm,
                packet.digest,
                parent["hash"],
                self.plan.proposal_cash,
            )
            try:
                proposal = self.proposer.propose(self.plan.campaign, round_number, arm, packet)
            except Exception as error:  # uncertain dispatch: the slot is consumed, no retry
                proposal = Proposal(None, "", None, "", f"{type(error).__name__}: {error}"[:200])
        else:  # crashed after consuming the slot: never re-call
            proposal = Proposal(None, "", None, "", "interrupted before resolution")
        obligation = f"proposal:{self.plan.campaign}:{round_number}:{arm}"
        if proposal.cost is not None:
            self.ledger.settle(
                obligation,
                proposal.cost,
                proposal.operation_id or obligation,
                obligation + "-settled",
            )
        content = proposal.content if proposal.content is not None else b""
        if proposal.content is not None and proposal.content == parent["content"].encode():
            content = b""  # an exact no-op is not a new candidate; the slot is consumed
        resolved = self.registry.resolve_slot(
            self.plan.campaign, round_number, arm, content, self.count, self.context
        )
        record = {
            **resolved,
            "hypothesis": proposal.hypothesis[:2000],
            "failure": proposal.failure
            or (None if resolved["status"] != "REJECTED" else "invalid skill"),
            "content": content.decode("utf-8", errors="replace"),
        }
        self._put(name, record)
        return record

    def freeze(self):
        frozen = self._get("frozen")
        if frozen:
            return frozen
        arms = {
            "S": self._get("S"),
            "B": self._get("B_best", self._get("S")),
            "R": self._get("R_incumbent", self._get("S")),
        }
        rng = random.Random(self.plan.order_seed)
        order = []
        for rep in range(self.plan.final_replicates):
            tasks = list(self.plan.final)
            rng.shuffle(tasks)
            for task in tasks:
                for arm in rng.sample(["S", "B", "R"], 3):
                    order.append([task, arm, rep])
        frozen = {
            "hashes": {a: arms[a]["hash"] for a in arms},
            "contents": {a: arms[a]["content"] for a in arms},
            "order": order,
            "order_hash": stable_hash(order),
        }
        self._put("frozen", frozen)
        return frozen

    def final(self):
        frozen = self.freeze()
        for task, arm, rep in frozen["order"]:
            self._rollout(task, frozen["contents"][arm].encode(), arm, "final", rep)
        self._put("final", "DONE")

    def progress(self) -> dict:
        """Neutral progress only: counts, never final outcomes before release."""
        rows = [
            json.loads(r[0]) for r in self.ledger.db.execute("SELECT result FROM campaign_rollouts")
        ]
        by_phase = {}
        for r in rows:
            by_phase[r["phase"]] = by_phase.get(r["phase"], 0) + 1
        return {
            "rollouts": by_phase,
            "rounds_done": [r for r in (1, 2, 3) if self._get(f"round{r}") == "DONE"],
            "final_done": self._get("final") == "DONE",
        }

    def report(self, sampling_justified=False, integrity_valid=True) -> dict:
        if self._get("final") != "DONE":
            raise ValueError("final outcomes are sealed until every final cell is terminal")
        frozen = self._get("frozen")
        k = self.plan.final_replicates
        cells = {}
        for task, arm, rep in frozen["order"]:
            result = self._rollout(task, frozen["contents"][arm].encode(), arm, "final", rep)
            cells.setdefault(task, {}).setdefault(arm, {})[rep] = result
        rows = []
        for task in self.plan.final:
            c = cells[task]
            rows.append(
                ReplicatedOutcome(
                    task,
                    self.plan.families[task],
                    tuple(c["S"][i].success for i in range(k)),
                    tuple(c["B"][i].success for i in range(k)),
                    tuple(c["R"][i].success for i in range(k)),
                    sum(c["S"][i].cost for i in range(k)),
                    sum(c["B"][i].cost for i in range(k)),
                    sum(c["R"][i].cost for i in range(k)),
                )
            )
        unsettled = self.ledger.unsettled()
        reconciled = not unsettled["leases"] and not unsettled["obligations"]
        report = replicated_screening_report(
            rows,
            list(self.plan.final),
            frozen["hashes"],
            k,
            sampling_justified=sampling_justified,
            integrity_valid=integrity_valid,
            reconciled=reconciled,
            research_cash=self.ledger.money()["settled"],
        )
        report["final_order_hash"] = frozen["order_hash"]
        report["interrupted_cells"] = sum(
            1
            for c in cells.values()
            for a in c.values()
            for r in a.values()
            if r.state.startswith("INTERRUPTED")
        )
        return report

    def run(self):
        self.baseline()
        for round_number in (1, 2, 3):
            self.round(round_number)
        self.final()
        return self.report()
