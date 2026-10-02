import json
import os
import time
import pytest
from recursive_run.contracts import State
from recursive_run.ledger import Ledger
from recursive_run.oracle import LexerContract
from recursive_run.workers import OfflineDockerRunner

# Explicit opt-in: real isolated container execution, distinct from real dataset intake.
pytestmark = pytest.mark.skipif(
    not os.environ.get("RL_OFFLINE_IMAGE"),
    reason="set RL_OFFLINE_IMAGE to a pulled digest to exercise Docker",
)


def execute(tmp_path, files, text="x", timeout=15):
    ledger = Ledger(tmp_path / "db")
    try:
        attempt = str(time.time_ns())
        ledger.create_attempt(
            attempt, "synthetic-worker-probe", "S", "smoke", time.time() + 60, "p", "offline"
        )
        ledger.transition(attempt, State.RUNNING, attempt + "-start")
        contract = LexerContract(module="probe", class_name="Lexer")
        runner = OfflineDockerRunner(os.environ["RL_OFFLINE_IMAGE"], timeout=timeout)
        result = runner.lex(files, contract, text, ledger=ledger, attempt_id=attempt)
        assert ledger.occupied_workers() == 0
        assert ledger.money() == {"reserved": 0, "settled": 0}
        return result
    finally:
        ledger.close()


def test_network_and_private_storage_absent(tmp_path):
    code = b"""import os, socket
class Lexer:
 def __init__(self, **options): pass
 def get_tokens_unprocessed(self, text):
  assert not os.path.exists('/workspace')
  assert not os.path.exists('/var/run/docker.sock')
  try:
   socket.create_connection(('1.1.1.1', 443), timeout=.2)
  except OSError: pass
  else: raise RuntimeError('network leaked')
  yield (0, 'Token.Text', text)
"""
    result = execute(tmp_path, {"probe.py": code}, "λ\r\n")
    assert result.status == "OK", result.stderr
    assert json.loads(result.output) == [[0, "Token.Text", "λ\r\n"]]
    assert result.removed


def test_timeout_and_output_flood_cleanup(tmp_path):
    slow = b"import time\ntime.sleep(60)\n"
    result = execute(tmp_path, {"probe.py": slow}, timeout=2)
    assert result.status == "TIMEOUT" and result.removed
    flood = b'import sys\nsys.stdout.write("x"*2000000)\nsys.stdout.flush()\n'
    result = execute(tmp_path, {"probe.py": flood})
    assert result.status == "MALFORMED" and result.removed


def test_oversized_input_and_snapshot_rejected(tmp_path):
    runner = OfflineDockerRunner(os.environ["RL_OFFLINE_IMAGE"])
    contract = LexerContract(module="probe", class_name="Lexer")
    with pytest.raises(ValueError):
        runner.lex({"../escape.py": b"x"}, contract, "x", ledger=None, attempt_id="a")
    with pytest.raises(ValueError):
        runner.lex({}, contract, "x" * 70000, ledger=None, attempt_id="a")


def test_false_pass_and_hidden_read_cannot_forge_verdict(tmp_path):
    from recursive_run.oracle import compare_outputs

    malicious = b"""import sys
assert not __import__('os').path.exists('/controller/private-oracle')
print('PASS')
class Lexer:
 def __init__(self,**options): pass
 def get_tokens_unprocessed(self,text):
  yield (0,'Token.Text',text)
"""
    result = execute(tmp_path, {"probe.py": malicious}, "visible")
    contract = LexerContract(module="probe", class_name="Lexer")
    expected = [b'[[0,"Token.Text","visible"]]', b'[[0,"Token.Text","private"]]']
    verdict = compare_outputs(expected, [result.output, result.output], contract)
    assert not verdict.success
    assert verdict.outcomes == ("MALFORMED", "MALFORMED")
