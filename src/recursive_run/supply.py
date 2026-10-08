"""Outcome-blind family supply and the eight-family effort checkpoint (design v1.1 §6).

Each repository profile names the files a repair may edit. Families are provisional:
files that one combined mutation edits together form one family, and two families
conflict when a module of one directly imports a module of the other. Independent supply
is a conflict-free set of families, so a shared importer such as pygments' templates.py
does not chain unrelated lexers together. Repositories never conflict with each other, so
supply adds across profiles. Source files are parsed with ast, never imported or executed.
The checkpoint reports a decision; choosing between stopping and a smaller descriptive
protocol stays with a reviewer.
"""

import ast
import math
import re
from collections import Counter
from typing import Literal

from pydantic import Field

from recursive_run.contracts import Record
from recursive_run.repositories import PROFILES, RepositoryProfile

REQUIRED = {"smoke": 8, "discovery": 24, "selection": 16, "final": 40}
MAXIMUM_SOURCE_BYTES = 2_000_000


def changed_files(patch: str) -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r"^diff --git a/(\S+) b/", patch, re.M))))


def module_imports(files: dict[str, bytes], profile: RepositoryProfile) -> dict[str, set[str]]:
    """Map each in-scope module path to the in-scope module paths it imports."""
    modules = {profile.module(path): path for path in files if profile.editable(path)}
    edges: dict[str, set[str]] = {path: set() for path in modules.values()}
    for name, path in modules.items():
        source = files[path]
        if len(source) > MAXIMUM_SOURCE_BYTES:
            raise ValueError("module source exceeds parse bound")
        package = name if path.endswith("__init__.py") else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(source, filename=path)):
            targets = []
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    if node.level - 1 >= len(parts):
                        continue
                    anchor = ".".join(parts[: len(parts) - (node.level - 1)])
                    base = f"{anchor}.{base}" if base else anchor
                targets = [base] + [f"{base}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            for target in targets:
                other = modules.get(target)
                if other and other != path:
                    edges[path].add(other)
    return edges


def lexer_imports(files: dict[str, bytes]) -> dict[str, set[str]]:
    return module_imports(files, PROFILES["pygments"])


class _Union:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: str, b: str):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def provisional_families(candidates: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Assign each candidate a family: files edited together by one mutation merge."""
    union = _Union()
    for paths in candidates.values():
        for other in paths[1:]:
            union.join(paths[0], other)
    return {task: union.find(paths[0]) for task, paths in candidates.items()}


def independent_families(
    candidates: dict[str, tuple[str, ...]], families: dict[str, str], imports: dict[str, set[str]]
) -> list[str]:
    """Deterministic greedy conflict-free family set; a lower bound on the true maximum."""
    members: dict[str, set[str]] = {}
    for task, paths in candidates.items():
        members.setdefault(families[task], set()).update(paths)
    owner = {path: family for family, paths in members.items() for path in paths}
    conflicts: dict[str, set[str]] = {family: set() for family in members}
    for path, targets in imports.items():
        for target in targets:
            a, b = owner.get(path), owner.get(target)
            if a and b and a != b:
                conflicts[a].add(b)
                conflicts[b].add(a)
    chosen, blocked = [], set()
    for family in sorted(members, key=lambda f: (len(conflicts[f]), f)):
        if family not in blocked:
            chosen.append(family)
            blocked |= conflicts[family] | {family}
    return chosen


def supply_report(
    rows: list[dict], files: dict[str, bytes], profile: RepositoryProfile = PROFILES["pygments"]
) -> dict:
    """Screen dataset rows to bounded in-scope edits and count independent families."""
    candidates: dict[str, tuple[str, ...]] = {}
    rejected = Counter()
    for row in rows:
        paths = changed_files(row["patch"])
        if not paths:
            rejected["empty patch"] += 1
        elif not all(profile.in_scope(p) for p in paths):
            rejected["edits outside repair scope"] += 1
        elif any(p in profile.excluded for p in paths):
            rejected[profile.excluded_reason] += 1
        elif not all(p in files for p in paths):
            rejected["path absent from reference"] += 1
        else:
            candidates[row["instance_id"]] = paths
    families = provisional_families(candidates)
    sizes = Counter(families.values())
    independent = independent_families(candidates, families, module_imports(files, profile))
    needed = sum(REQUIRED.values())
    return {
        "schema_version": 1,
        "repository": profile.repository,
        "source_revision": profile.reference,
        "grouping_rule": "co-edited files merge; direct-import conflicts excluded; greedy v1",
        "rows_screened": len(rows),
        "structural_candidates": len(candidates),
        "rejections": dict(rejected),
        "provisional_families": len(sizes),
        "independent_supply": len(independent),
        "required_independent_families": needed,
        "structural_upper_bound_met": len(independent) >= needed,
        "independent_families": independent,
        "family_sizes": dict(sorted(sizes.items(), key=lambda x: (-x[1], x[0]))),
        "oracle_eligibility_verified": False,
        "grouping_reviewed": False,
    }


class EffortReceipt(Record):
    family_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    preparation_hours: float = Field(ge=0)
    review_hours: float = Field(ge=0)
    admitted: bool
    rejection_reason: str | None = None
    reviewer_id: str = Field(min_length=1)


class CheckpointDecision(Record):
    decision: Literal["BLOCKED", "PROCEED", "REVISE_PROTOCOL"]
    reasons: tuple[str, ...]
    receipts: int
    admitted: int
    acceptance_rate: float | None
    hours_per_admitted_family: float | None
    families_still_needed: int
    projected_candidates_to_review: int | None
    projected_hours: float | None
    effort_limit_hours: float
    independent_supply: int


def checkpoint(
    receipts: list[EffortReceipt], independent_supply: int, effort_limit_hours: float
) -> CheckpointDecision:
    """Apply the design's after-eight-families checkpoint without choosing STOP itself."""
    if effort_limit_hours <= 0:
        raise ValueError("effort limit must be frozen before the checkpoint")
    needed = sum(REQUIRED.values())
    families = {r.family_id for r in receipts}
    admitted = [r for r in receipts if r.admitted]
    admitted_families = {r.family_id for r in admitted}
    remaining = needed - len(admitted_families)
    base = dict(
        receipts=len(receipts),
        admitted=len(admitted),
        families_still_needed=remaining,
        effort_limit_hours=effort_limit_hours,
        independent_supply=independent_supply,
    )
    if len(families) < REQUIRED["smoke"] or len(admitted_families) < REQUIRED["smoke"]:
        return CheckpointDecision(
            decision="BLOCKED",
            reasons=("eight admitted distinct families with effort receipts are required",),
            acceptance_rate=None,
            hours_per_admitted_family=None,
            projected_candidates_to_review=None,
            projected_hours=None,
            **base,
        )
    hours = sum(r.preparation_hours + r.review_hours for r in receipts)
    rate = len(admitted_families) / len(families)
    per_family = hours / len(admitted_families)
    to_review = math.ceil(remaining / rate)
    projected = round(per_family * remaining, 2)
    unreviewed = independent_supply - len(families)
    reasons = []
    if to_review > unreviewed:
        reasons.append(
            f"need about {to_review} more families at the observed acceptance rate; "
            f"only {unreviewed} unreviewed independent families remain"
        )
    if projected > effort_limit_hours:
        reasons.append(f"projected {projected} hours exceeds the {effort_limit_hours}-hour limit")
    return CheckpointDecision(
        decision="REVISE_PROTOCOL" if reasons else "PROCEED",
        reasons=tuple(reasons) or ("supply and projected effort fit the frozen limit",),
        acceptance_rate=round(rate, 4),
        hours_per_admitted_family=round(per_family, 2),
        projected_candidates_to_review=to_review,
        projected_hours=projected,
        **base,
    )
