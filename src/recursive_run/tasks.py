"""Frozen intake/split contracts; valid schemas do not substitute for scientific review."""

from typing import Literal
from pydantic import Field, model_validator
from recursive_run.contracts import Record
from recursive_run.intake import regular_path
from recursive_run.oracle import CallContract, LexerContract
from recursive_run.repositories import PROFILES


class PrivateCheck(Record):
    check_id: str = Field(min_length=1)
    category: Literal["distinguishing", "regression"]
    input_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    newly_authored: bool


class OracleSpec(Record):
    oracle_id: str = Field(min_length=1)
    evaluator_revision: str = Field(min_length=1)
    contract: LexerContract | CallContract
    checks: tuple[PrivateCheck, ...]
    intake_receipt_ref: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_completeness(self):
        if len({check.check_id for check in self.checks}) != len(self.checks):
            raise ValueError("duplicate private check identity")
        if not any(c.category == "distinguishing" and c.newly_authored for c in self.checks):
            raise ValueError("newly authored distinguishing input required")
        if not any(c.category == "regression" and c.newly_authored for c in self.checks):
            raise ValueError("independent newly authored regression input required")
        if len({c.input_ref for c in self.checks}) < 2:
            raise ValueError("distinguishing and regression inputs must be distinct")
        return self


class IntakeReceipt(Record):
    receipt_id: str
    task_id: str
    source_revision: str
    dataset_row_hash: str
    origin: Literal["procedural", "model-rewritten", "combined", "mirrored-pr"]
    license_ids: tuple[str, ...]
    attribution_ref: str
    upstream_reference: str
    reference_hash: str
    broken_hash: str
    reverse_patch_hash: str
    reverse_chain_verified: bool
    reference_passed: bool
    broken_failed: bool
    deterministic: bool
    oracle_scope_reviewed: bool
    grouping_reviewed: bool
    dataset_membership_verified: bool
    reviewer_id: str
    preparation_hours: float = Field(ge=0)
    review_hours: float = Field(ge=0)

    @property
    def accepted(self):
        return bool(self.license_ids and self.reviewer_id) and all(
            [
                self.reverse_chain_verified,
                self.reference_passed,
                self.broken_failed,
                self.deterministic,
                self.oracle_scope_reviewed,
                self.grouping_reviewed,
                self.dataset_membership_verified,
            ]
        )


class TaskSpec(Record):
    task_id: str
    repository: str = "pygments"
    dataset_revision: str
    dataset_row_hash: str
    reference_commit: str
    broken_commit: str
    broken_snapshot_ref: str
    visible_problem_ref: str
    allowed_paths: tuple[str, ...]
    family_id: str
    relationship_entities: tuple[str, ...]
    mutation_ancestors: tuple[str, ...]
    oracle_id: str
    intake_receipt_id: str

    @model_validator(mode="after")
    def scope(self):
        if not self.allowed_paths or len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ValueError("nonempty unique production edit scope required")
        profile = PROFILES.get(self.repository)
        if profile is None or not profile.admitted:
            raise ValueError("repository is not an admitted task source")
        for name in self.allowed_paths:
            regular_path(name)
            if not profile.editable(name):
                raise ValueError("path outside the repository's repair scope")
        return self


class PartitionSpec(Record):
    revision: str
    source_revision: str
    group_map_hash: str
    eligibility_receipts_hash: str
    draw_method: Literal["random", "purposive"]
    seed: int | None
    intended_population: str
    sampling_justified_receipt: str | None
    substitution_policy_hash: str
    smoke: tuple[str, ...]
    discovery: tuple[str, ...]
    selection: tuple[str, ...]
    final: tuple[str, ...]
    task_families: dict[str, str]
    generation_maximum: Literal[0] = 0

    @model_validator(mode="after")
    def independent_partitions(self):
        groups = [self.smoke, self.discovery, self.selection, self.final]
        if tuple(map(len, groups)) != (8, 24, 16, 40):
            raise ValueError("default protocol requires 8/24/16/40; revise protocol explicitly")
        all_tasks = [task for group in groups for task in group]
        if len(set(all_tasks)) != 88 or set(self.task_families) != set(all_tasks):
            raise ValueError("overlap or incomplete group membership")
        seen = set()
        for group in groups:
            families = {self.task_families[task] for task in group}
            if len(families) != len(group) or seen & families:
                raise ValueError("independent families required without cross-partition siblings")
            seen |= families
        if self.draw_method == "random" and self.seed is None:
            raise ValueError("random assignment requires a frozen seed")
        return self
