"""Outcome-blind source inventory, before eligibility/oracle acceptance.

Git mirror candidates are genuine source mutations but remain provisional until their
frozen dataset rows, provenance, grouping and intake receipts have been verified.
"""

import json
import re
import subprocess
from pathlib import Path

from recursive_run.artifacts import digest
from recursive_run.intake import regular_path, validate_patch

UPSTREAM_REFERENCE = "27649ebbf5a2519725036b48ec99ef7745f100af"
REFERENCE = "d53d06ab2c467be43d32f68881422698ee33adf6"
MIRROR = "https://github.com/swesmith/pygments__pygments.27649ebb.git"


def git(root: Path, *arguments: str, maximum_bytes=16_777_216) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[:2000])
    if len(result.stdout) > maximum_bytes:
        raise ValueError("git result exceeds bound")
    return result.stdout


def snapshot_files(root: Path, revision: str) -> dict[str, bytes]:
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("snapshot requires full immutable commit")
    files = {}
    for item in git(root, "ls-tree", "-rz", revision).split(b"\0"):
        if not item:
            continue
        metadata, raw_name = item.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        name = regular_path(raw_name.decode("utf-8"))
        # Supply only package code + preserved license; no tests, metadata, history or golden assets.
        if not (name.startswith("pygments/") or name in {"LICENSE", "AUTHORS"}):
            continue
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("nonregular file in executable snapshot")
        files[name] = git(root, "cat-file", "blob", object_id)
    return files


def inspect_candidate(root: Path, broken: str, task_id: str) -> dict:
    for revision in [REFERENCE, broken]:
        if not re.fullmatch("[0-9a-f]{40}", revision):
            raise ValueError("invalid revision")
        git(root, "cat-file", "-e", revision + "^{commit}")
    patch = git(root, "diff", "--no-ext-diff", "--no-renames", "--no-textconv", REFERENCE, broken)
    reverse = git(root, "diff", "--no-ext-diff", "--no-renames", "--no-textconv", broken, REFERENCE)
    changed = (
        git(root, "diff", "--name-only", "--no-renames", REFERENCE, broken).decode().splitlines()
    )
    production = bool(changed) and all(
        re.fullmatch(r"pygments/lexers/[a-zA-Z0-9_]+\.py", p) for p in changed
    )
    reason = None
    if production:
        try:
            validate_patch(patch, set(changed))
            validate_patch(reverse, set(changed))
        except ValueError as error:
            reason = str(error)
    else:
        reason = "not exclusively bounded lexer production edits"
    # Grouping by file is conservative provisional screening, not a final family map.
    return {
        "schema_version": 1,
        "task_id": task_id,
        "reference": REFERENCE,
        "broken": broken,
        "mirror": MIRROR,
        "changed_paths": changed,
        "patch_hash": digest(patch),
        "reverse_patch_hash": digest(reverse),
        "provisional_group": "|".join(sorted(changed)),
        "production_candidate": production and reason is None,
        "rejection": reason,
        "dataset_membership_verified": False,
        "intake_accepted": False,
    }


def write_inventory(root: Path, heads: Path, output: Path):
    records = []
    for line in heads.read_text().splitlines():
        commit, ref = line.split("\t")
        if ref != "refs/heads/main":
            records.append(
                {
                    "task_id": ref.removeprefix("refs/heads/"),
                    "broken": commit,
                    "status": "uninspected",
                    "dataset_membership_verified": False,
                }
            )
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mirror": MIRROR,
                "heads_hash": digest(heads.read_bytes()),
                "reference": REFERENCE,
                "candidate_branches": len(records),
                "accepted_tasks": 0,
                "eligible_independent_families": None,
                "records": records,
            },
            indent=2,
        )
        + "\n"
    )
