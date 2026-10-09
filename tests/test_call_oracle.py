import json
import pytest
from pydantic import ValidationError
from recursive_run.oracle import CallContract, compare_outputs
from recursive_run.repositories import PROFILES
from recursive_run.tasks import TaskSpec


@pytest.fixture
def contract():
    return CallContract(
        pipeline="sqlglot.transpile", options={"read": "snowflake", "write": "sqlite"}
    )


def test_pipeline_options_are_allowlisted():
    with pytest.raises(ValidationError):
        CallContract(pipeline="sqlglot.transpile", options={"schema": {"t": {"a": "INT"}}})
    with pytest.raises(ValidationError):
        CallContract(pipeline="os.system")
    assert CallContract(pipeline="sqlglot.optimize", options={"schema": {"t": {"a": "INT"}}})


def test_exact_text_and_error_class_comparison(contract):
    expected = [b'{"ok":["SELECT 1"]}', b'{"error":"ParseError"}']
    assert compare_outputs(expected, expected, contract).success
    assert not compare_outputs(expected, [b'{"ok":["SELECT 1 "]}', expected[1]], contract).success
    # A broken repair that stops raising is a behavior change, not a pass.
    assert not compare_outputs(expected, [expected[0], b'{"ok":[""]}'], contract).success
    assert not compare_outputs(expected, [expected[0]], contract).success
    assert not compare_outputs(expected, [None, expected[1]], contract).success


@pytest.mark.parametrize(
    "data",
    [
        b"PASS",
        b"NaN",
        b'["SELECT 1"]',
        b'{"ok":"SELECT 1"}',
        b'{"ok":[1]}',
        b'{"ok":[],"error":"X"}',
        b'{"error":"not an identifier"}',
        b'{"verdict":"PASS"}',
    ],
)
def test_malformed_call_output_never_passes(contract, data):
    expected = [b'{"ok":["SELECT 1"]}', b'{"ok":["SELECT 2"]}']
    result = compare_outputs(expected, [data, expected[1]], contract)
    assert result.outcomes[0] == "MALFORMED" and not result.success


def test_task_paths_follow_repository_profile():
    base = dict(
        task_id="t",
        dataset_revision="r",
        dataset_row_hash="h",
        reference_commit="a",
        broken_commit="b",
        broken_snapshot_ref="s",
        visible_problem_ref="v",
        family_id="f",
        relationship_entities=(),
        mutation_ancestors=(),
        oracle_id="o",
        intake_receipt_id="i",
    )
    assert TaskSpec(repository="sqlglot", allowed_paths=("sqlglot/dialects/sqlite.py",), **base)
    assert TaskSpec(allowed_paths=("pygments/lexers/sql.py",), **base)
    for repository, path in [
        ("sqlglot", "tests/test_sqlite.py"),
        ("sqlglot", "sqlglot/__main__.py"),
        ("pygments", "pygments/lexers/_mapping.py"),
        ("pygments", "sqlglot/parser.py"),
        ("mistune", "src/mistune/core.py"),  # profiled, not admitted
        ("unknown", "x.py"),
    ]:
        with pytest.raises(ValidationError):
            TaskSpec(repository=repository, allowed_paths=(path,), **base)


def test_snapshot_excludes_tests_and_history():
    profile = PROFILES["sqlglot"]
    assert profile.snapshot("sqlglot/parser.py") and profile.snapshot("LICENSE")
    assert not profile.snapshot("tests/test_parser.py")
    assert not profile.snapshot(".git/config")
    assert not profile.snapshot("sqlglotrs/src/lib.rs")


def test_patch_round_trip_through_validator_and_worker(tmp_path):
    import base64
    import subprocess
    import sys

    from recursive_run.intake import validate_patch
    from recursive_run.patches import make_patch
    from recursive_run.workers import WORKER_CODE

    before = {
        "pkg/a.py": b"x = 1\ny = 2\nz = 3\n",
        "pkg/b.py": b"no newline",
        "pkg/c.py": b"same\n",
    }
    after = {
        "pkg/a.py": b"x = 1\ny = 20\nz = 3\nw = 4\n",
        "pkg/b.py": b"no newline, still\n",
        "pkg/c.py": b"same\n",
    }
    patch = make_patch(before, after)
    assert validate_patch(patch, {"pkg/a.py", "pkg/b.py"}) == {"pkg/a.py", "pkg/b.py"}
    assert make_patch(before, before) == b""
    # Synthetic files only: the reviewed worker applies the patch and returns the bytes.
    request = {
        "files": {k: base64.b64encode(v).decode() for k, v in before.items()},
        "patches": [base64.b64encode(patch).decode()],
        "return_files": sorted(after),
        "contract": {},
        "input": "",
    }
    done = subprocess.run(
        [sys.executable, "-I", "-c", WORKER_CODE],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    rebuilt = {k: base64.b64decode(v) for k, v in json.loads(done.stdout).items()}
    assert rebuilt == after


def _worker(tmp_path, request, code=None):
    import subprocess
    import sys

    from recursive_run.workers import WORKER_CODE

    return subprocess.run(
        [sys.executable, "-I", "-c", code or WORKER_CODE],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=30,
        cwd=tmp_path,
    )


def test_staged_snapshot_location_is_restricted(tmp_path):
    request = {"files": {}, "contract": {}, "input": "", "snapshot_dir": str(tmp_path)}
    done = _worker(tmp_path, request)
    assert done.returncode != 0 and b"unreviewed snapshot location" in done.stderr


def test_staged_snapshot_is_copied_then_overridden_and_patched(tmp_path):
    import base64

    from recursive_run.patches import make_patch
    from recursive_run.workers import WORKER_CODE

    staged = tmp_path / "snapshots" / "base"
    (staged / "pkg").mkdir(parents=True)
    (staged / "pkg" / "a.py").write_bytes(b"x = 1\n")
    (staged / "pkg" / "b.py").write_bytes(b"y = 2\n")
    patch = make_patch({"pkg/a.py": b"x = 10\n"}, {"pkg/a.py": b"x = 11\n"})
    request = {
        "files": {"pkg/a.py": base64.b64encode(b"x = 10\n").decode()},
        "patches": [base64.b64encode(patch).decode()],
        "return_files": ["pkg/a.py", "pkg/b.py"],
        "contract": {},
        "input": "",
        "snapshot_dir": str(staged),
    }
    # Point the reviewed prefix at the test directory; the logic under test is unchanged.
    code = WORKER_CODE.replace("/home/user/rl/snapshots/", str(tmp_path / "snapshots") + "/")
    done = _worker(tmp_path, request, code)
    assert done.returncode == 0, done.stderr
    rebuilt = {k: base64.b64decode(v) for k, v in json.loads(done.stdout).items()}
    assert rebuilt == {"pkg/a.py": b"x = 11\n", "pkg/b.py": b"y = 2\n"}
    assert (staged / "pkg" / "a.py").read_bytes() == b"x = 1\n"  # staged copy untouched


def test_lexer_crash_is_inert_data_and_fails_the_check(tmp_path):
    import base64

    from recursive_run.oracle import LexerContract

    crashing = b"class Lexer:\n def __init__(self, **o): pass\n def get_tokens_unprocessed(self, t):\n  raise KeyError('x')\n"
    contract = LexerContract(module="probe", class_name="Lexer")
    request = {
        "files": {"probe.py": base64.b64encode(crashing).decode()},
        "contract": contract.model_dump(),
        "input": "x",
        "patches": [],
    }
    done = _worker(tmp_path, request)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"error": "KeyError"}
    expected = [b'[[0,"Token.Text","x"]]', b'[[0,"Token.Text","y"]]']
    result = compare_outputs(expected, [done.stdout, expected[1]], contract)
    assert result.outcomes[0] == "MALFORMED" and not result.success
