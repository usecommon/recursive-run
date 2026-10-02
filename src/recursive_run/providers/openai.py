"""Pinned OpenAI coding-call adapter, no SDK retry or extra response-repair call."""

import json
import math
import os
from decimal import Decimal, ROUND_CEILING

from recursive_run.gateway import DispatchCapabilities, ModelResult
from recursive_run.ledger import Blocked

MODEL = "gpt-4.1-mini-2025-04-14"
MAX_INPUT_TOKENS = 32768
MAX_OUTPUT_TOKENS = 2048
# Published standard text-token rates, micro-USD/token; cached discounts accounted separately.
INPUT_RATE = Decimal("0.4")
CACHED_INPUT_RATE = Decimal("0.1")
OUTPUT_RATE = Decimal("1.6")
MAXIMUM_CALL_CASH = math.ceil(MAX_INPUT_TOKENS * INPUT_RATE + MAX_OUTPUT_TOKENS * OUTPUT_RATE)
PRICE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"


def token_cash(prompt: int, completion: int, cached: int = 0) -> int:
    if any(type(x) is not int or x < 0 for x in (prompt, completion, cached)) or cached > prompt:
        raise ValueError("invalid provider usage")
    return int(
        (
            (prompt - cached) * INPUT_RATE + cached * CACHED_INPUT_RATE + completion * OUTPUT_RATE
        ).to_integral_value(rounding=ROUND_CEILING)
    )


def encode_request(messages: list[dict]) -> bytes:
    clean = []
    for item in messages:
        if (
            item.get("role") not in {"system", "user", "assistant"}
            or type(item.get("content")) is not str
        ):
            raise ValueError("unsupported message format")
        clean.append({"role": item["role"], "content": item["content"]})
    import tiktoken

    encoder = tiktoken.encoding_for_model(MODEL)
    # Conservative message overhead. No tools/images/audio are enabled in this protocol.
    tokens = sum(len(encoder.encode(x["content"], disallowed_special=())) + 16 for x in clean) + 64
    if tokens > MAX_INPUT_TOKENS:
        raise Blocked("request exceeds frozen conservative input bound")
    request = {
        "model": MODEL,
        "messages": clean,
        "temperature": 0,
        "max_completion_tokens": MAX_OUTPUT_TOKENS,
        "n": 1,
        "response_format": {"type": "json_object"},
    }
    return json.dumps(request, sort_keys=True, separators=(",", ":")).encode()


class OpenAIProvider:
    capabilities = DispatchCapabilities(False, False, True, "openai-chat-no-retry-v1")

    def __init__(self, client=None):
        if client is None:
            key = os.environ.get("RL_MODEL_API_KEY")
            if not key:
                raise Blocked("RL_MODEL_API_KEY is not configured")
            from openai import OpenAI

            client = OpenAI(api_key=key, max_retries=0, timeout=60)
        if client.max_retries != 0:
            raise Blocked("OpenAI SDK retries must be disabled")
        self.client = client

    def generate(self, admitted, request: bytes) -> ModelResult:
        payload = json.loads(request)
        if set(payload) != {
            "model",
            "messages",
            "temperature",
            "max_completion_tokens",
            "n",
            "response_format",
        }:
            raise Blocked("unfrozen request fields")
        if (
            payload["model"] != MODEL
            or payload["max_completion_tokens"] != MAX_OUTPUT_TOKENS
            or payload["n"] != 1
            or payload["temperature"] != 0
            or payload["response_format"] != {"type": "json_object"}
        ):
            raise Blocked("request differs from pinned settings")
        if admitted.maximum_cost < MAXIMUM_CALL_CASH:
            raise Blocked("insufficient upper-bound reservation")
        # Provider exceptions propagate as uncertain dispatch; the gateway retains the reservation.
        response = self.client.chat.completions.create(**payload)
        usage = response.usage
        if usage is None or not response.id:
            raise Blocked("provider response lacks attributable usage/id")
        cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
        cash = token_cash(usage.prompt_tokens, usage.completion_tokens, cached)
        if usage.prompt_tokens > MAX_INPUT_TOKENS or usage.completion_tokens > MAX_OUTPUT_TOKENS:
            raise Blocked("provider usage exceeds frozen bounds; retain obligation for review")
        if len(response.choices) != 1:
            raise Blocked("unexpected completion count")
        choice = response.choices[0]
        text = choice.message.content or ""
        # Preserve billed malformed/truncated content; parsing happens after gateway settlement.
        output = json.dumps(
            {
                "role": "assistant",
                "content": text,
                "extra": {"finish_reason": choice.finish_reason},
            },
            separators=(",", ":"),
        ).encode()
        return ModelResult(
            response.id,
            output,
            cash,
            {
                "input_tokens": usage.prompt_tokens,
                "cached_input_tokens": cached,
                "output_tokens": usage.completion_tokens,
            },
        )


def parse_agent_response(payload: bytes) -> dict:
    envelope = json.loads(payload)
    if envelope["extra"]["finish_reason"] != "stop":
        raise ValueError("truncated or incomplete completion")
    response = json.loads(envelope["content"])
    if type(response) is not dict or set(response) != {"thought", "actions"}:
        raise ValueError("expected exactly thought/actions")
    actions = response["actions"]
    if (
        type(response["thought"]) is not str
        or type(actions) is not list
        or not 1 <= len(actions) <= 4
    ):
        raise ValueError("invalid coding response")
    for action in actions:
        if (
            type(action) is not dict
            or set(action) != {"command"}
            or type(action["command"]) is not str
        ):
            raise ValueError("unsupported action")
    envelope["extra"]["actions"] = actions
    return envelope
