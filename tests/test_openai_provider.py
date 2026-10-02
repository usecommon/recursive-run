import json
import time
import httpx
import pytest
from openai import OpenAI
from recursive_run.artifacts import digest
from recursive_run.contracts import AdmittedAction
from recursive_run.providers.openai import (
    OpenAIProvider,
    token_cash,
    MODEL,
    MAXIMUM_CALL_CASH,
    parse_agent_response,
)


def envelope(request):
    return AdmittedAction(
        experiment_id="e",
        attempt_id="a",
        operation_key="k",
        action_digest=digest(request),
        policy_hash="p",
        cancellation_epoch=0,
        generation=0,
        deadline=time.time() + 60,
        reservation_id="r",
        lease_id="w",
        maximum_cost=MAXIMUM_CALL_CASH,
        kind="model",
    )


def request():
    return json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "synthetic mock task"}],
            "temperature": 0,
            "max_completion_tokens": 2048,
            "n": 1,
            "response_format": {"type": "json_object"},
        }
    ).encode()


def test_cost_upper_bound_and_cache_accounting():
    assert MAXIMUM_CALL_CASH * 20 <= 500000
    assert token_cash(100, 20, 10) == 69
    with pytest.raises(ValueError):
        token_cash(10, 1, 20)


def test_sdk_response_usage_and_parse_without_extra_calls():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"thought": "mock", "actions": [{"command": "submit"}]}
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_tokens_details": {"cached_tokens": 10},
                },
            },
        )

    client = OpenAI(
        api_key="synthetic-test-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        data = request()
        result = OpenAIProvider(client).generate(envelope(data), data)
        assert result.actual_cash == 69 and result.provider_operation_id == "chatcmpl-mock"
        assert parse_agent_response(result.output)["extra"]["actions"] == [{"command": "submit"}]
        assert len(calls) == 1
    finally:
        client.close()


def test_http_failure_is_not_automatically_retried():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, json={"error": {"message": "mock failure"}})

    client = OpenAI(
        api_key="synthetic-test-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        data = request()
        with pytest.raises(Exception):
            OpenAIProvider(client).generate(envelope(data), data)
        assert len(calls) == 1
    finally:
        client.close()
