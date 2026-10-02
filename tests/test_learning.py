import sqlite3
import pytest
from recursive_run.learning import (
    Evidence,
    History,
    PackingPolicy,
    pack,
    coding_schedule,
    Candidate,
    SelectionVector,
    canonical_skill,
    reuse_key,
    REUSE_FIELDS,
    LearningRegistry,
)
from recursive_run.ledger import Ledger


def count(data):
    # Synthetic test tokenizer, explicitly not a pinned model tokenizer.
    return len(data)


def evidence(round_number):
    return [Evidence(f"S{i}", str(i), "S", f"trace {i}") for i in range(round_number * 8)] + [
        Evidence(f"R{i}", str(i + 8), "R", f"R trace {i}") for i in range((round_number - 1) * 8)
    ]


def test_matched_evidence_and_reserved_history():
    policy = PackingPolicy(20000, 1200, 1000, 30000, "whole-record-prefix-v1", "test-byte-count")
    history = [
        History(1, 1, "R", {"hypothesis": "prior"}),
        History(1, 2, "B", {"hypothesis": "FORBIDDEN B"}),
        History(2, 3, "R", {"hypothesis": "FORBIDDEN CURRENT"}),
    ]
    b = pack(2, "B", evidence(2), b"S", history, policy, count)
    r = pack(2, "R", evidence(2), b"R1", history, policy, count)
    assert b.common == r.common and b.common_hash == r.common_hash
    assert b.history == b"[]"
    assert b"prior" in r.history and b"FORBIDDEN" not in r.history
    assert b"prior" not in b.common


def test_unauthorized_history_and_evidence_rejected():
    policy = PackingPolicy(20000, 1200, 1000, 30000, "v1", "test")
    with pytest.raises(ValueError):
        pack(
            2,
            "R",
            evidence(2),
            b"parent",
            [History(1, 1, "R", {"private_input": "canary"})],
            policy,
            count,
        )
    wrong = evidence(1)
    wrong[0] = Evidence("private", "task", "S", "canary", "selection")
    with pytest.raises(ValueError):
        pack(1, "B", wrong, b"S", [], policy, count)


def test_exact_identity_rank_and_workload():
    assert sum(coding_schedule().values()) == 280
    assert canonical_skill(b"a", count)[1] != canonical_skill(b"a ", count)[1]
    with pytest.raises(ValueError):
        canonical_skill(b"x" * 1201, count)
    vector = SelectionVector((True,) * 8 + (False,) * 8, 100)
    shorter = Candidate(b"a", "a", 1, 1, vector)
    longer = Candidate(b"ab", "ab", 2, 0, vector)
    assert shorter.rank < longer.rank
    assert Candidate(b"a", "a", 1, 0, vector).rank < shorter.rank


def test_scoped_alias_and_consumed_invalid_slot(tmp_path):
    ledger = Ledger(tmp_path / "db")
    try:
        registry = LearningRegistry(ledger)
        context = {key: key for key in REUSE_FIELDS}
        context["campaign"] = "c"
        for arm in ["B", "R"]:
            registry.consume_slot("c", 1, arm, "packet", "S", 100)
        b = registry.resolve_slot("c", 1, "B", b"skill", count, context)
        r = registry.resolve_slot("c", 1, "R", b"skill", count, context)
        assert b["status"] == "NEEDS_SELECTION" and r["status"] == "WAITING_ALIAS"
        registry.store_selection(
            b["reuse_key"], b["content_hash"], SelectionVector((True,) * 16, 5)
        )
        assert (
            ledger.db.execute("SELECT status FROM proposals WHERE arm='R'").fetchone()[0]
            == "ALIASED"
        )
        with pytest.raises(sqlite3.IntegrityError):
            registry.consume_slot("c", 1, "B", "new packet", "S", 100)
        registry.consume_slot("c", 2, "B", "p", "S", 100)
        assert registry.resolve_slot("c", 2, "B", b"", count, context)["status"] == "REJECTED"
        with pytest.raises(ValueError):
            registry.resolve_slot("c", 2, "B", b"fixed", count, context)
        assert reuse_key("content", context) != reuse_key(
            "content", {**context, "campaign": "other"}
        )
    finally:
        ledger.close()
