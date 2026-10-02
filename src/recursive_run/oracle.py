"""Trusted comparison of bounded inert JSON; never import candidate modules here."""

import json
from pydantic import Field
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


def parse_tokens(payload: bytes, contract: LexerContract) -> list[list[int | str]]:
    if len(payload) > contract.maximum_output_bytes:
        raise ValueError("oversized output")

    def no_constant(value):
        raise ValueError("nonfinite JSON")

    try:
        value = json.loads(payload, parse_constant=no_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid inert JSON") from error
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
    contract: LexerContract,
    forbidden_edit: bool = False,
) -> OracleResult:
    if len(expected) < 2 or len(actual) > len(expected):
        raise ValueError("invalid required-check count")
    outcomes = []
    for reference, candidate in zip(expected, actual, strict=False):
        frozen = parse_tokens(reference, contract)
        if candidate is None:
            outcomes.append("WORKER_ERROR")
            continue
        try:
            observed = parse_tokens(candidate, contract)
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
