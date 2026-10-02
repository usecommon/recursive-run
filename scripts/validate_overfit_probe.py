"""Real-source wrong repair passes one visible example and fails private scoring.

Private inputs stay in ignored controller storage. Snapshot reconstruction and all
candidate execution occur in isolated workers; the host only prepares inert patch bytes.
"""

import argparse
import difflib
import json
import time
from pathlib import Path
from recursive_run.artifacts import ArtifactStore, digest
from recursive_run.contracts import State
from recursive_run.inventory import snapshot_files, REFERENCE
from recursive_run.ledger import Ledger
from recursive_run.oracle import LexerContract, compare_outputs, parse_tokens
from recursive_run.workers import OfflineDockerRunner

IMAGE = (
    "ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    case = json.loads(args.inputs.read_text())[0]
    contract = LexerContract.model_validate(case["contract"])
    reference = snapshot_files(args.source, REFERENCE)
    broken = snapshot_files(args.source, case["broken_commit"])
    ledger = Ledger(args.output / "ledger.sqlite")
    artifacts = ArtifactStore(args.output / "artifacts")
    runner = OfflineDockerRunner(IMAGE)

    def execute(label, files, text, patches=()):
        attempt = f"{label}-{time.time_ns()}"
        ledger.create_attempt(
            attempt, case["task_id"], "S", "smoke", time.time() + 60, "overfit", "offline"
        )
        ledger.transition(attempt, State.RUNNING, attempt + "-start")
        result = runner.lex(
            files,
            contract,
            text,
            ledger=ledger,
            attempt_id=attempt,
            patches=patches,
            allowed_paths={"pygments/lexers/configs.py"},
        )
        key = artifacts.put(
            json.dumps(
                {
                    "status": result.status,
                    "output": result.output.decode(errors="replace"),
                    "stderr": result.stderr.decode(errors="replace"),
                }
            ).encode()
        )
        ledger.register_artifact(key, "private", attempt)
        return result

    try:
        visible = 'config VISIBLE_PILOT\n    bool "Visible demo"\n'
        published = execute("visible-reference", reference, visible)
        assert published.status == "OK"
        tokens = parse_tokens(published.output, contract)
        # Explicitly fixture-only bad repair: hardcodes the visible example and emits no other tokens.
        target = "pygments/lexers/configs.py"
        before = broken[target].decode()
        suffix = (
            "\n\ndef _pilot_overfit(self, text):\n    if text == "
            + repr(visible)
            + ":\n        yield from "
            + repr([tuple(x) for x in tokens])
            + "\nKconfigLexer.get_tokens_unprocessed = _pilot_overfit\n"
        )
        after = before + suffix
        delta = (
            "diff --git a/"
            + target
            + " b/"
            + target
            + "\n"
            + "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="a/" + target,
                    tofile="b/" + target,
                )
            )
        )
        patch = delta.encode()
        visible_candidate = execute("visible-candidate", broken, visible, (patch,))
        assert parse_tokens(visible_candidate.output, contract) == tokens
        private_expected = []
        private_actual = []
        for index, text in enumerate(case["inputs"]):
            r = execute(f"private-reference-{index}", reference, text)
            c = execute(f"private-candidate-{index}", broken, text, (patch,))
            assert r.status == "OK"
            private_expected.append(r.output)
            private_actual.append(c.output if c.status == "OK" else None)
        score = compare_outputs(private_expected, private_actual, contract)
        receipt = {
            "schema_version": 1,
            "task_id": case["task_id"],
            "fixture_only": True,
            "visible_example_passed": True,
            "private_scoring_passed": score.success,
            "expected_checks": score.expected_checks,
            "completed_checks": score.completed_checks,
            "patch_hash": digest(patch),
            "unresolved_workers": ledger.occupied_workers(),
            "outcomes": score.outcomes,
        }
        assert not score.success and score.completed_checks == 2 and ledger.occupied_workers() == 0
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt))
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
