import sqlite3
import pytest
from recursive_run.contracts import Manifest
from recursive_run.experiments import ExperimentStore
from recursive_run.ledger import Ledger, Blocked
from recursive_run.tasks import PartitionSpec, OracleSpec, PrivateCheck
from recursive_run.oracle import LexerContract


def partition():
    ids = [str(x) for x in range(88)]
    return dict(
        revision="v1",
        source_revision="source",
        group_map_hash="groups",
        eligibility_receipts_hash="receipts",
        draw_method="purposive",
        seed=None,
        intended_population="eligible lexer repairs",
        sampling_justified_receipt=None,
        substitution_policy_hash="none",
        smoke=ids[:8],
        discovery=ids[8:32],
        selection=ids[32:48],
        final=ids[48:],
        task_families={x: x for x in ids},
    )


def test_group_before_split_and_full_membership():
    data = partition()
    assert PartitionSpec(**data).generation_maximum == 0
    data["task_families"]["48"] = "0"
    with pytest.raises(ValueError):
        PartitionSpec(**data)
    data = partition()
    data["final"] = data["final"][:-1]
    with pytest.raises(ValueError):
        PartitionSpec(**data)


def test_oracle_receipt_requires_distinct_new_private_inputs():
    c = LexerContract(module="pygments.lexers.python", class_name="PythonLexer")
    checks = [
        PrivateCheck(
            check_id="d",
            category="distinguishing",
            input_ref="a" * 64,
            expected_ref="b" * 64,
            newly_authored=True,
        ),
        PrivateCheck(
            check_id="r",
            category="regression",
            input_ref="c" * 64,
            expected_ref="d" * 64,
            newly_authored=True,
        ),
    ]
    assert (
        len(
            OracleSpec(
                oracle_id="o",
                evaluator_revision="v1",
                contract=c,
                checks=tuple(checks),
                intake_receipt_ref="e" * 64,
            ).checks
        )
        == 2
    )
    with pytest.raises(ValueError):
        OracleSpec(
            oracle_id="o",
            evaluator_revision="v1",
            contract=c,
            checks=(checks[0],),
            intake_receipt_ref="e" * 64,
        )


def test_manifest_is_immutable_and_live_start_cannot_bypass_missing_bindings(tmp_path):
    ledger = Ledger(tmp_path / "db")
    try:
        store = ExperimentStore(ledger)
        manifest = Manifest(experiment_id="e", stage="RL0", mode="mock")
        store.freeze(manifest)
        with pytest.raises(sqlite3.IntegrityError):
            store.freeze(manifest)
        with pytest.raises(Blocked):
            store.start("e", "smoke", "start")
        assert store.start("e", "offline", "start") == {"started": True}
        assert store.start("e", "offline", "start") == {"started": True}
        with pytest.raises(Blocked):
            store.start("e", "offline", "again")
    finally:
        ledger.close()
