import json
import subprocess
import sys
from pathlib import Path

import pytest

import recursive_run.live as live
from recursive_run.contracts import Manifest
from recursive_run.gateway import ModelResult
from recursive_run.ledger import Ledger
from recursive_run.learning import Packet
from recursive_run.live import (
    LiveProposer,
    LiveRollouts,
    TaskStore,
    extract_evidence,
    leaks,
    learning_gates,
    parse_proposal,
)
from recursive_run.oracle import compare_outputs

TASK = "tobymao__sqlglot.036601ba.func_pm_ctrl_shuffle__abcdefgh"
HIDDEN_IN = "SELECT DATEDIFF(MONTH, x, y) FROM secret_table_name"
EXPECTED = '{"ok": ["SELECT MONTHS_BETWEEN(y, x) FROM secret_table_name"]}'


def store():
    return TaskStore(
        drafts={
            "func_pm_ctrl_shuffle__abcdefgh": {
                "pipeline": "sqlglot.transpile",
                "options": {"read": "spark", "write": "spark"},
                "distinguishing": HIDDEN_IN,
                "regression": "SELECT 1 AS regression_probe_value",
            }
        },
        frozen={
            TASK: {
                "broken_files": {"sqlglot/a.py": "broken\n"},
                "expected": [EXPECTED, '{"ok": ["SELECT 1"]}'],
            }
        },
        rows={
            TASK: {
                "patch": "diff --git a/sqlglot/a.py b/sqlglot/a.py\n",
                "problem_statement": "DATEDIFF breaks.",
            }
        },
        references={"sqlglot": {"sqlglot/a.py": b"fixed\n", "sqlglot/b.py": b"other\n"}},
        families={TASK: "sqlglot/a.py"},
    )


def test_task_store_overlays_broken_files_and_keeps_inputs_private():
    task = store().get(TASK)
    assert task.broken == {"sqlglot/a.py": b"broken\n", "sqlglot/b.py": b"other\n"}
    assert task.task.contract.pipeline == "sqlglot.transpile"
    assert HIDDEN_IN in task.hidden() and EXPECTED in task.hidden()
    with pytest.raises(ValueError):
        live.repository_for("someone__else.1234.func_x")


def trace(*commands, observation="ok"):
    messages = [{"role": "system", "content": "s"}]
    for c in commands:
        messages += [
            {"role": "assistant", "content": "", "extra": {"actions": [{"command": c}]}},
            {"role": "user", "content": observation},
        ]
    return {"messages": messages}


def test_evidence_reports_categories_and_withholds_on_canary_overlap():
    task = store().get(TASK)
    record = {
        "oracle": {"outcomes": ["FAIL", "PASS"], "forbidden_edit": False},
        "state": "FAILED",
        "success": False,
        "exit_status": "Submitted",
        "steps": 7,
        "edited": ["sqlglot/a.py"],
        "patch": "--- a\n+++ b\n+fix\n",
        "trace": trace("rg DATEDIFF sqlglot", "python -c 'import sqlglot'"),
    }
    text = extract_evidence(task, record)
    assert "distinguishing=FAIL, regression=PASS" in text and "$ rg DATEDIFF sqlglot" in text
    assert len(text) <= live.EVIDENCE_CHARS and not leaks(text, task.hidden())
    # If the agent happened to type a protected input, only the categories survive.
    leaked = extract_evidence(task, {**record, "trace": trace(f"echo '{HIDDEN_IN}'")})
    assert "withheld" in leaked and HIDDEN_IN not in leaked and "regression=PASS" in leaked
    assert "rg DATEDIFF" not in leaked


class FakeRunner:
    def __init__(self):
        self.released = []

    def stage(self, lease, files, target=None):
        pass

    def release(self, lease):
        self.released.append(lease)


def manifest():
    return Manifest(experiment_id="rl1-test", stage="RL1", mode="mock")


def rollouts(tmp_path, monkeypatch, *, fail_coding=False, outcome=("PASS", "PASS")):
    ledger = Ledger(tmp_path / "db", cap=280_000_000)
    monkeypatch.setattr(
        live, "_lease", lambda runner, attempt, purpose, life: f"{attempt}-{purpose}"
    )

    def fake_attempt(manifest, operation, model, environment, task, skill, templates, root, **kw):
        assert operation in {"learning", "final"} and "tip" in skill
        if fail_coding:
            raise RuntimeError("sandbox lost")
        return {"claim": {"exit_status": "Submitted"}, "trace": trace("rg x")}

    monkeypatch.setattr(live, "run_attempt", fake_attempt)
    monkeypatch.setattr(
        live,
        "export_patch",
        lambda r, lease, broken, p: (b"--- a\n+++ b\n", ["sqlglot/a.py"], [], []),
    )
    task = store().get(TASK)

    def fake_score(runner, ledger, attempt, broken, patch, allowed, t, expected, life, forbidden):
        observed = [x if o == "PASS" else b'{"ok": ["wrong"]}' for x, o in zip(expected, outcome)]
        return compare_outputs(expected, observed, t.contract)

    monkeypatch.setattr(live, "score", fake_score)
    runner = FakeRunner()
    port = LiveRollouts(
        ledger, runner, None, manifest(), store(), {}, {"coding": 600, "scoring": 120}, tmp_path
    )
    return ledger, runner, port, task


def test_live_rollout_scores_privately_and_reports_cost(tmp_path, monkeypatch):
    ledger, runner, port, _ = rollouts(tmp_path, monkeypatch)
    try:
        result = port.run("k" * 32, TASK, b"skill tip", "R", "discovery", 0)
        assert result.success and result.state == "SUCCEEDED" and result.arm == "R"
        assert "verified: PASSED" in result.evidence and runner.released
        attempt = ledger.db.execute("SELECT arm, phase FROM attempts").fetchone()
        assert tuple(attempt) == ("R", "discovery")
        selection = port.run("s" * 32, TASK, b"skill tip", "B", "selection", 2)
        assert selection.evidence == ""  # selection traces never reach the learner
        with pytest.raises(Exception):  # same key again: never re-run
            port.run("k" * 32, TASK, b"skill tip", "R", "discovery", 0)
    finally:
        ledger.close()


def test_live_rollout_failure_still_scores_and_counts(tmp_path, monkeypatch):
    ledger, runner, port, _ = rollouts(
        tmp_path, monkeypatch, fail_coding=True, outcome=("FAIL", "PASS")
    )
    try:
        result = port.run("f" * 32, TASK, b"tip", "S", "final", 1)
        assert not result.success and result.state == "FAILED" and result.evidence == ""
        assert runner.released == [f"final-S-{'f' * 20}-coding"]
    finally:
        ledger.close()


def packet():
    return Packet(b"[]", b"parent skill", b"[]", "c", (), (), 10, "d")


class FakeProvider:
    def __init__(self, content, finish="stop", error=None):
        self.content, self.finish, self.error, self.calls = content, finish, error, 0

    def generate(self, admitted, request):
        self.calls += 1
        assert admitted.maximum_cost >= 16_384 and b"parent skill" in request
        if self.error:
            raise self.error
        output = json.dumps(
            {"role": "assistant", "content": self.content, "extra": {"finish_reason": self.finish}}
        )
        return ModelResult("op-1", output.encode(), 1234, {})


def test_proposer_one_call_parses_or_records_failure():
    ok = FakeProvider(json.dumps({"hypothesis": "verify first", "skill": "New skill."}))
    proposal = LiveProposer(ok, 2_000_000).propose("c", 1, "R", packet())
    assert (proposal.content, proposal.hypothesis, proposal.cost, proposal.failure) == (
        b"New skill.",
        "verify first",
        1234,
        None,
    )
    bad = LiveProposer(FakeProvider("not json"), 2_000_000).propose("c", 1, "B", packet())
    assert bad.content is None and bad.cost == 1234 and "malformed" in bad.failure
    assert (
        parse_proposal(
            json.dumps(
                {"role": "a", "content": "{}", "extra": {"finish_reason": "length"}}
            ).encode()
        )[2]
        == "truncated reply"
    )
    extra = json.dumps({"hypothesis": "h", "skill": "s", "x": 1})
    assert (
        parse_proposal(json.dumps({"content": extra, "extra": {"finish_reason": "stop"}}).encode())[
            0
        ]
        is None
    )
    lost = FakeProvider("", error=TimeoutError("reply lost"))
    with pytest.raises(TimeoutError):  # the campaign keeps the slot consumed and reserved
        LiveProposer(lost, 2_000_000).propose("c", 2, "R", packet())
    assert lost.calls == 1
    with pytest.raises(ValueError):
        LiveProposer(ok, 1)


def test_gates_block_until_review_storage_and_authorization():
    decisions = json.loads(Path("configs/rl1-decisions.json").read_text())
    pool = json.loads(Path("configs/task-pool-v1.json").read_text())
    partition = json.loads(Path("configs/partition-v1.json").read_text())
    gates = learning_gates(decisions, pool, partition)
    assert len(gates) == 4
    reviewed = {**pool, "tasks": [{**t, "human_reviewed": True} for t in pool["tasks"]]}
    ready = {
        **decisions,
        "learning_authorized": True,
        "durable_storage": {"verified": True, "receipt": "r"},
    }
    assert learning_gates(ready, reviewed, {**partition, "status": "BINDING"}) == []
    out = subprocess.run(
        [sys.executable, "scripts/run_campaign.py", "--check"], capture_output=True, text=True
    )
    assert out.returncode == 1 and json.loads(out.stdout)["ready"] is False
    refused = subprocess.run(
        [sys.executable, "scripts/run_campaign.py"], capture_output=True, text=True
    )
    assert refused.returncode != 0 and "blocked" in refused.stderr
