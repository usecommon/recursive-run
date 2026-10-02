"""Two genuine public mirror mutations; private input/output files remain in .runtime.

Their dataset row linkage and final task grouping remain unverified. This command is
bounded preparatory evidence, not an authorized live smoke or full intake acceptance.
"""

import argparse
import json
import time
from pathlib import Path

from recursive_run.artifacts import digest, ArtifactStore
from recursive_run.contracts import State
from recursive_run.inventory import snapshot_files, git, REFERENCE, UPSTREAM_REFERENCE
from recursive_run.ledger import Ledger
from recursive_run.oracle import LexerContract, compare_outputs, parse_tokens
from recursive_run.workers import OfflineDockerRunner

IMAGE = (
    "ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cases", type=int, choices=[2, 8], default=2)
    parser.add_argument(
        "--inputs", type=Path, required=True, help="controller-private authored intake specs"
    )
    parser.add_argument("--output", type=Path, default=Path(".runtime/offline-pairs"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = Ledger(args.output / "ledger.sqlite")
    store = ArtifactStore(args.output / "artifacts")
    runner = OfflineDockerRunner(IMAGE)
    receipts = []
    preparation_started = time.monotonic()
    try:
        reference = snapshot_files(args.source, REFERENCE)
        # Mirror reference differs from upstream only in excluded workflow metadata.
        assert git(args.source, "rev-parse", REFERENCE + ":pygments") == git(
            args.source, "rev-parse", UPSTREAM_REFERENCE + ":pygments"
        )

        def execute(
            task, label, files, contract, text="", patches=(), allowed_paths=None, hash_only=False
        ):
            attempt = task + "-" + label + "-" + str(time.time_ns())
            ledger.create_attempt(
                attempt,
                task,
                "S",
                "smoke",
                time.time() + 60,
                contract.content_hash,
                "offline-real-pairs",
            )
            ledger.transition(attempt, State.RUNNING, attempt + "-start")
            result = runner.lex(
                files,
                contract,
                text,
                ledger=ledger,
                attempt_id=attempt,
                patches=patches,
                allowed_paths=allowed_paths,
                hash_only=hash_only,
            )
            # Retain protected raw diagnostics, without printing oracle material.
            key = store.put(
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

        cases = json.loads(args.inputs.read_text())
        if len(cases) < args.cases:
            raise ValueError("insufficient private intake specs")
        for case in cases[: args.cases]:
            task, broken_commit, inputs = case["task_id"], case["broken_commit"], case["inputs"]
            contract = LexerContract.model_validate(case["contract"])
            broken = snapshot_files(args.source, broken_commit)
            changed = {name for name in reference if reference[name] != broken[name]}
            forward = git(
                args.source,
                "diff",
                "--no-ext-diff",
                "--no-renames",
                "--no-textconv",
                REFERENCE,
                broken_commit,
            )
            reverse = git(
                args.source,
                "diff",
                "--no-ext-diff",
                "--no-renames",
                "--no-textconv",
                broken_commit,
                REFERENCE,
            )
            chain = execute(
                task,
                "reverse-chain",
                reference,
                contract,
                patches=(forward, reverse),
                allowed_paths=changed,
                hash_only=True,
            )
            chain_valid = chain.status == "OK" and json.loads(chain.output) == {
                k: digest(v) for k, v in reference.items()
            }
            expected, observed = [], []
            deterministic = True
            statuses = []
            for index, text in enumerate(inputs):
                outputs = []
                for mode, files in [
                    ("reference", reference),
                    ("broken", broken),
                    ("reference-repeat", reference),
                    ("broken-repeat", broken),
                ]:
                    result = execute(task, f"{mode}-{index}", files, contract, text)
                    outputs.append(result)
                    statuses.append(result.status)
                r, b, rr, br = outputs
                if r.status != "OK" or rr.status != "OK":
                    raise RuntimeError(
                        "reference failed offline worker check; see protected evidence"
                    )
                parse_tokens(r.output, contract)
                expected.append(r.output)
                observed.append(b.output if b.status == "OK" else None)
                deterministic &= (
                    r.output == rr.output and b.status == br.status and b.output == br.output
                )
            score = compare_outputs(expected, observed, contract)
            reference_score = compare_outputs(expected, expected, contract)
            # Actual reverse reconstruction + scoring in a separate worker (not the reference snapshot).
            restored = [
                execute(
                    task,
                    f"restored-{i}",
                    broken,
                    contract,
                    text,
                    patches=(reverse,),
                    allowed_paths=changed,
                )
                for i, text in enumerate(inputs)
            ]
            restored_score = compare_outputs(
                expected, [x.output if x.status == "OK" else None for x in restored], contract
            )
            receipt = {
                "schema_version": 1,
                "task_id": task,
                "reference": REFERENCE,
                "upstream_reference": UPSTREAM_REFERENCE,
                "broken": broken_commit,
                "contract_hash": contract.content_hash,
                "image_digest": IMAGE,
                "patch_hash": digest(forward),
                "reverse_patch_hash": digest(reverse),
                "allowed_paths": sorted(changed),
                "reverse_chain_exact": chain_valid,
                "reference_passes": reference_score.success,
                "broken_distinguished": not score.success,
                "deterministic": deterministic,
                "restored_passes": restored_score.success,
                "expected_checks": len(inputs),
                "input_hashes": [digest(x.encode()) for x in inputs],
                "input_origin": "newly-authored-for-offline-intake",
                "dataset_membership_verified": False,
                "family_map_frozen": False,
                "intake_accepted": False,
                "private_inputs_excluded_from_worker_snapshot": True,
                "status_counts": {s: statuses.count(s) for s in set(statuses)},
                "human_review_hours": None,
                "external_cash": "not measured; existing cloud-instance offline compute",
                "live_expiry_verified": False,
                "smoke_exposed_family": sorted(changed),
                "preparation_elapsed_seconds": time.monotonic() - preparation_started,
            }
            receipts.append(receipt)
            print(
                json.dumps(
                    {
                        key: receipt[key]
                        for key in [
                            "task_id",
                            "reverse_chain_exact",
                            "reference_passes",
                            "broken_distinguished",
                            "deterministic",
                            "restored_passes",
                            "intake_accepted",
                        ]
                    }
                )
            )
        (args.output / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
        if any(
            not all(
                r[k]
                for k in [
                    "reverse_chain_exact",
                    "reference_passes",
                    "broken_distinguished",
                    "deterministic",
                    "restored_passes",
                ]
            )
            for r in receipts
        ):
            raise SystemExit(1)
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
