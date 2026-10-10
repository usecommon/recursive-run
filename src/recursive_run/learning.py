"""Deterministic RL1 schedule, packets, canonical identity and selection.

No model is called here. A proposer adapter must consume exactly one durable slot.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Callable


def stable_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Evidence:
    reference: str
    task: str
    arm: str
    text: str
    visibility: str = "discovery"


@dataclass(frozen=True)
class History:
    round: int
    occurrence: int
    origin_arm: str
    fields: dict


@dataclass(frozen=True)
class Packet:
    common: bytes
    parent: bytes
    history: bytes
    common_hash: str
    included: tuple[str, ...]
    omitted: tuple[str, ...]
    tokens: int
    digest: str


@dataclass(frozen=True)
class PackingPolicy:
    common_tokens: int
    parent_tokens: int
    history_tokens: int
    input_tokens: int
    extraction_version: str
    tokenizer_id: str


HISTORY_FIELDS = {
    "content",
    "diff",
    "hypothesis",
    "source_references",
    "promotion",
    "selection_successes",
    "selection_cost",
    "failure",
    "alias",
}


def _encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def pack(
    round_number: int,
    arm: str,
    evidence: list[Evidence],
    parent: bytes,
    history: list[History],
    policy: PackingPolicy,
    count: Callable[[bytes], int],
) -> Packet:
    if arm not in {"B", "R"} or round_number not in {1, 2, 3}:
        raise ValueError("invalid arm/round")
    if count(parent) > policy.parent_tokens:
        raise ValueError("parent exceeds reserved allowance")
    expected_counts = {1: {"S": 8, "R": 0}, 2: {"S": 16, "R": 8}, 3: {"S": 24, "R": 16}}
    observed = {source: sum(x.arm == source for x in evidence) for source in ("S", "R")}
    if observed != expected_counts[round_number] or any(
        x.visibility != "discovery" for x in evidence
    ):
        raise ValueError("unauthorized or unscheduled common evidence")
    if len({x.reference for x in evidence}) != len(evidence):
        raise ValueError("duplicate evidence reference")
    common_records = []
    included, omitted = [], []
    truncating = False
    for item in evidence:
        record = {
            "reference": item.reference,
            "task": item.task,
            "source_arm": item.arm,
            "text": item.text,
        }
        if truncating or count(_encode(common_records + [record])) > policy.common_tokens:
            omitted.append(item.reference)
            truncating = True  # deterministic whole-record prefix, never outcome-driven.
        else:
            common_records.append(record)
            included.append(item.reference)
    common = _encode(common_records)
    eligible = sorted(
        [x for x in history if x.origin_arm == "R" and x.round < round_number],
        key=lambda x: (x.round, x.occurrence),
    )
    if any(set(x.fields) - HISTORY_FIELDS for x in eligible):
        raise ValueError("unauthorized history fields")
    records = (
        [{"round": x.round, "occurrence": x.occurrence, **x.fields} for x in eligible]
        if arm == "R"
        else []
    )
    # Oldest-first removal of complete records after common + parent reservation.
    while records and count(_encode(records)) > policy.history_tokens:
        records.pop(0)
    history_bytes = _encode(records)
    serialized = _encode(
        {
            "common": common.decode(),
            "parent": parent.decode("utf-8"),
            "history": history_bytes.decode(),
            "version": policy.extraction_version,
        }
    )
    tokens = count(serialized)
    if tokens > policy.input_tokens:
        raise ValueError("complete request exceeds frozen input ceiling")
    return Packet(
        common,
        parent,
        history_bytes,
        hashlib.sha256(common).hexdigest(),
        tuple(included),
        tuple(omitted),
        tokens,
        hashlib.sha256(serialized).hexdigest(),
    )


@dataclass(frozen=True)
class SelectionVector:
    outcomes: tuple[bool, ...]
    total_cost: int

    def __post_init__(self):
        if len(self.outcomes) != 16 or any(type(x) is not bool for x in self.outcomes):
            raise ValueError("selection requires exactly 16 complete assigned outcomes")
        if self.total_cost < 0:
            raise ValueError("negative cost")


@dataclass(frozen=True)
class Candidate:
    content: bytes
    content_hash: str
    tokens: int
    version: int
    selection: SelectionVector

    @property
    def rank(self):
        return (-sum(self.selection.outcomes), self.selection.total_cost, self.tokens, self.version)


def canonical_skill(content: bytes, count: Callable[[bytes], int]) -> tuple[bytes, str, int]:
    # Exact UTF8 bytes: no whitespace rewriting or semantic equivalence judgment.
    content.decode("utf-8", errors="strict")
    if not content.strip() or b"\x00" in content:
        raise ValueError("empty/invalid skill")
    tokens = count(content)
    if tokens > 1200:
        raise ValueError("skill exceeds 1200 receiving-model tokens")
    return content, hashlib.sha256(content).hexdigest(), tokens


REUSE_FIELDS = {
    "campaign",
    "panel",
    "draw",
    "model",
    "settings",
    "harness",
    "adapter",
    "image",
    "oracle",
    "split",
    "limits",
    "assistance",
}


def reuse_key(content_hash: str, context: dict[str, str]) -> str:
    if set(context) != REUSE_FIELDS or any(not value for value in context.values()):
        raise ValueError("incomplete canonical reuse context")
    return stable_hash({"content": content_hash, **context})


def coding_schedule(selection_replicates: int = 1, final_replicates: int = 1) -> dict[str, int]:
    """Maximum coding rollouts. Defaults are design v1.1 (280). RL1 decisions use 3 and 3:
    every selection and final task/arm cell is run that many times in fresh sessions."""
    if not (1 <= selection_replicates <= 5 and 1 <= final_replicates <= 5):
        raise ValueError("replicates must be frozen between 1 and 5")
    return {
        "smoke": 8,
        "baseline_discovery": 24,
        "baseline_selection": 16 * selection_replicates,
        "cumulative_discovery": 16,
        "candidate_selection_maximum": 96 * selection_replicates,
        "final": 120 * final_replicates,
    }


class LearningRegistry:
    """Durable slots and canonical selection draws on the controller's writer connection."""

    def __init__(self, ledger):
        self.ledger = ledger
        ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS proposals(
          campaign TEXT NOT NULL, round INTEGER NOT NULL, arm TEXT NOT NULL,
          status TEXT NOT NULL, packet_hash TEXT NOT NULL, parent_hash TEXT NOT NULL,
          content_hash TEXT, reuse_key TEXT, rejection TEXT,
          PRIMARY KEY(campaign, round, arm));
        CREATE TABLE IF NOT EXISTS selections(
          reuse_key TEXT PRIMARY KEY, content_hash TEXT NOT NULL, vector TEXT NOT NULL);
        """)

    def consume_slot(
        self, campaign, round_number, arm, packet_hash, parent_hash, maximum_cash: int
    ):
        if round_number not in {1, 2, 3} or arm not in {"B", "R"}:
            raise ValueError("invalid proposal slot")
        with self.ledger.transaction():
            self.ledger._reserve(
                f"proposal:{campaign}:{round_number}:{arm}", None, "proposal", maximum_cash
            )
            self.ledger.db.execute(
                "INSERT INTO proposals VALUES(?,?,?,'DISPATCH_INTENT',?,?,NULL,NULL,NULL)",
                (campaign, round_number, arm, packet_hash, parent_hash),
            )
            self.ledger._event(
                None,
                "proposal_slot",
                {"campaign": campaign, "round": round_number, "arm": arm, "packet": packet_hash},
            )
        # A crash or failed call consumes this row; no automatic retry.

    def resolve_slot(self, campaign, round_number, arm, content, count, context):
        context = dict(context)
        if context.get("campaign") != campaign:
            raise ValueError("cross-campaign alias context")
        with self.ledger.transaction():
            row = self.ledger.db.execute(
                "SELECT * FROM proposals WHERE campaign=? AND round=? AND arm=?",
                (campaign, round_number, arm),
            ).fetchone()
            if not row or row["status"] != "DISPATCH_INTENT":
                raise ValueError("unknown or resolved slot")
            try:
                _, content_hash, _ = canonical_skill(content, count)
                key = reuse_key(content_hash, context)
            except (ValueError, UnicodeDecodeError) as error:
                self.ledger.db.execute(
                    "UPDATE proposals SET status='REJECTED',rejection=? WHERE campaign=? AND round=? AND arm=?",
                    (str(error), campaign, round_number, arm),
                )
                return {"status": "REJECTED"}
            existing = self.ledger.db.execute(
                "SELECT * FROM selections WHERE reuse_key=?", (key,)
            ).fetchone()
            pending = self.ledger.db.execute(
                "SELECT 1 FROM proposals WHERE reuse_key=? AND status='NEEDS_SELECTION'", (key,)
            ).fetchone()
            status = "ALIASED" if existing else ("WAITING_ALIAS" if pending else "NEEDS_SELECTION")
            self.ledger.db.execute(
                "UPDATE proposals SET status=?,content_hash=?,reuse_key=? WHERE campaign=? AND round=? AND arm=?",
                (status, content_hash, key, campaign, round_number, arm),
            )
            return {"status": status, "reuse_key": key, "content_hash": content_hash}

    def store_selection(self, key, content_hash, vector: SelectionVector):
        with self.ledger.transaction():
            self.ledger.db.execute(
                "INSERT INTO selections VALUES(?,?,?)",
                (
                    key,
                    content_hash,
                    _encode({"outcomes": vector.outcomes, "cost": vector.total_cost}).decode(),
                ),
            )
            self.ledger.db.execute(
                "UPDATE proposals SET status=CASE WHEN status='WAITING_ALIAS' THEN 'ALIASED' ELSE 'SELECTED' END WHERE reuse_key=? AND status IN ('WAITING_ALIAS','NEEDS_SELECTION')",
                (key,),
            )
