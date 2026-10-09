"""Trusted comparison of bounded inert JSON; never import candidate modules here."""

import json
from typing import Literal
from pydantic import Field, model_validator
from recursive_run.contracts import Record, OracleResult
from recursive_run.artifacts import digest


class LexerContract(Record):
    module: str
    class_name: str
    options: dict[str, bool | int | str] = Field(default_factory=dict)
    api: str = "get_tokens_unprocessed"
    encoding: str = "utf-8"
    reset: str = "fresh-process-and-lexer-per-input"
    maximum_input_bytes: int = 65536
    maximum_output_bytes: int = 1048576
    maximum_tokens: int = 65536

    @property
    def content_hash(self) -> str:
        return digest(self.model_dump_json().encode())

    def parse(self, payload: bytes):
        return parse_tokens(payload, self)


# Reviewed text-in/text-out entry points, executed only by the isolated worker. Each
# pipeline returns a list of strings or the bare exception class name it raised.
PIPELINES = {
    "sqlglot.transpile": {"read", "write", "pretty", "identify"},
    "sqlglot.optimize": {"dialect", "schema", "pretty", "isolate_tables"},
    "sqlglot.qualify": {"dialect", "schema", "pretty", "isolate_tables"},
    "sqlglot.execute": {"tables_json", "read"},
    "mistune.html": {"escape", "hard_wrap", "plugins"},
}
Option = bool | int | str | tuple[str, ...] | dict[str, dict[str, str]]


class CallContract(Record):
    pipeline: Literal[
        "sqlglot.transpile",
        "sqlglot.optimize",
        "sqlglot.qualify",
        "sqlglot.execute",
        "mistune.html",
    ]
    options: dict[str, Option] = Field(default_factory=dict)
    encoding: str = "utf-8"
    reset: str = "fresh-process-per-input"
    maximum_input_bytes: int = 65536
    maximum_output_bytes: int = 1048576
    maximum_items: int = 4096

    @model_validator(mode="after")
    def known_options(self):
        if not set(self.options) <= PIPELINES[self.pipeline]:
            raise ValueError("option not accepted by pipeline")
        return self

    @property
    def content_hash(self) -> str:
        return digest(self.model_dump_json().encode())

    def parse(self, payload: bytes):
        return parse_call_result(payload, self)


Contract = LexerContract | CallContract


def _inert_json(payload: bytes, maximum_bytes: int):
    if len(payload) > maximum_bytes:
        raise ValueError("oversized output")

    def no_constant(value):
        raise ValueError("nonfinite JSON")

    try:
        return json.loads(payload, parse_constant=no_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid inert JSON") from error


def parse_call_result(payload: bytes, contract: CallContract) -> dict:
    value = _inert_json(payload, contract.maximum_output_bytes)
    if type(value) is not dict or len(value) != 1:
        raise ValueError("invalid call result")
    if "ok" in value:
        items = value["ok"]
        if type(items) is not list or len(items) > contract.maximum_items:
            raise ValueError("invalid result list")
        if not all(type(item) is str for item in items):
            raise ValueError("non-string result item")
    elif "error" in value:
        if type(value["error"]) is not str or not value["error"].isidentifier():
            raise ValueError("invalid error class name")
    else:
        raise ValueError("invalid call result")
    return value


def parse_tokens(payload: bytes, contract: LexerContract) -> list[list[int | str]]:
    value = _inert_json(payload, contract.maximum_output_bytes)
    if type(value) is not list or len(value) > contract.maximum_tokens:
        raise ValueError("invalid token list")
    previous = -1
    for token in value:
        if type(token) is not list or len(token) != 3:
            raise ValueError("invalid token triple")
        offset, kind, text = token
        if type(offset) is not int or offset < previous or offset < 0:
            raise ValueError("invalid offset/order")
        if type(kind) is not str or not kind.startswith("Token") or type(text) is not str:
            raise ValueError("invalid token type/text")
        previous = offset
    return value


def compare_outputs(
    expected: list[bytes],
    actual: list[bytes | None],
    contract: Contract,
    forbidden_edit: bool = False,
) -> OracleResult:
    if len(expected) < 2 or len(actual) > len(expected):
        raise ValueError("invalid required-check count")
    outcomes = []
    for reference, candidate in zip(expected, actual, strict=False):
        frozen = contract.parse(reference)
        if candidate is None:
            outcomes.append("WORKER_ERROR")
            continue
        try:
            observed = contract.parse(candidate)
        except ValueError:
            outcomes.append("MALFORMED")
        else:
            outcomes.append("PASS" if observed == frozen else "FAIL")
    return OracleResult(
        expected_checks=len(expected),
        completed_checks=len(outcomes),
        outcomes=tuple(outcomes),
        forbidden_edit=forbidden_edit,
        evaluator_hash=contract.content_hash,
    )
