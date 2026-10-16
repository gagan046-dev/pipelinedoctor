from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from fakes import FakeLLM
from pipelinedoctor.engine.llm import (
    EXTRA_BODY,
    EXTRA_HEADERS,
    LiteLLMClient,
    LLMError,
    RequestBudget,
    call_with_fallback,
    profile_for,
)
from pipelinedoctor.engine.parse import extract_json

MODELS = [
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
]


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_budget_caps_and_spaces_requests() -> None:
    clock = Clock()
    budget = RequestBudget(2, min_interval_s=3.5, clock=clock, sleep=clock.sleep)
    assert budget.acquire()
    clock.now += 1.0
    assert budget.acquire()
    assert clock.slept == [pytest.approx(2.5)]
    assert not budget.acquire()
    assert budget.remaining == 0 and budget.used == 2


def test_profiles() -> None:
    assert profile_for(MODELS[0]).json_mode is True
    assert profile_for(MODELS[1]).json_mode is False
    assert profile_for("some/other-model").json_mode is False


def _parse(text: str) -> dict[str, Any]:
    return extract_json(text)


def test_first_model_answers() -> None:
    llm = FakeLLM({"ok": True})
    result = call_with_fallback(llm, MODELS, "s", "u", RequestBudget(5), 60, _parse)
    assert result.value == {"ok": True}
    assert [a.outcome for a in result.attempts] == ["ok"]
    assert llm.calls[0].json_mode is True
    assert result.calls == 1


@pytest.mark.parametrize(
    ("first", "outcome"),
    [
        (LLMError("timeout", "slow"), "timeout"),
        (LLMError("rate_limited", "429"), "rate_limited"),
        ("I cannot answer that.", "invalid_json"),
    ],
)
def test_falls_back_to_second_model(first: object, outcome: str) -> None:
    llm = FakeLLM(first, {"ok": 2})  # type: ignore[arg-type]
    result = call_with_fallback(llm, MODELS, "s", "u", RequestBudget(5), 60, _parse)
    assert result.value == {"ok": 2}
    assert [a.outcome for a in result.attempts] == [outcome, "ok"]
    assert llm.calls[1].model == MODELS[1] and llm.calls[1].json_mode is False
    assert result.response is not None and result.response.model == MODELS[1]


def test_all_models_fail_never_exceeds_two_calls() -> None:
    llm = FakeLLM(LLMError("provider_error", "500"), LLMError("timeout", "slow"), {"never": 1})
    result = call_with_fallback(llm, MODELS, "s", "u", RequestBudget(10), 60, _parse)
    assert result.value is None
    assert len(llm.calls) == 2
    assert len(result.errors) == 2


def test_budget_exhaustion_stops_calls() -> None:
    llm = FakeLLM(LLMError("timeout", "slow"), {"ok": 1})
    result = call_with_fallback(llm, MODELS, "s", "u", RequestBudget(1), 60, _parse)
    assert result.value is None
    assert [a.outcome for a in result.attempts] == ["timeout", "budget_exhausted"]
    assert result.calls == 1


def test_rate_limited_model_is_skipped_until_cooldown() -> None:
    clock = Clock()
    budget = RequestBudget(10, clock=clock, sleep=clock.sleep, rate_limit_cooldown_s=60)
    llm = FakeLLM(LLMError("rate_limited", "429"), {"ok": 1}, {"ok": 2}, {"ok": 3})
    first = call_with_fallback(llm, MODELS, "s", "u", budget, 60, _parse)
    assert first.value == {"ok": 1} and first.calls == 2
    second = call_with_fallback(llm, MODELS, "s", "u", budget, 60, _parse)
    assert second.value == {"ok": 2} and second.calls == 1
    assert llm.calls[-1].model == MODELS[1]
    assert "skipped, rate limited" in second.errors[0]
    clock.now += 61
    third = call_with_fallback(llm, MODELS, "s", "u", budget, 60, _parse)
    assert third.value == {"ok": 3} and llm.calls[-1].model == MODELS[0]
    assert budget.used == 4


# --------------------------------------------------------------------------- LiteLLM adapter


class _FakeTimeout(Exception):
    pass


class _FakeRateLimit(Exception):
    pass


def _install_fake_litellm(monkeypatch: pytest.MonkeyPatch, behaviour: object) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    module = types.ModuleType("litellm")
    exceptions = types.ModuleType("litellm.exceptions")
    exceptions.Timeout = _FakeTimeout  # type: ignore[attr-defined]
    exceptions.RateLimitError = _FakeRateLimit  # type: ignore[attr-defined]
    module.exceptions = exceptions  # type: ignore[attr-defined]

    def completion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        if isinstance(behaviour, Exception):
            raise behaviour
        message = types.SimpleNamespace(content='{"ok": true}')
        usage = types.SimpleNamespace(prompt_tokens=11, completion_tokens=7)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=usage)

    def completion_cost(completion_response: Any) -> float:
        raise ValueError("model not in price map")

    module.completion = completion  # type: ignore[attr-defined]
    module.completion_cost = completion_cost  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", module)
    monkeypatch.setitem(sys.modules, "litellm.exceptions", exceptions)
    return seen


def test_litellm_client_sends_contract_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_fake_litellm(monkeypatch, None)
    response = LiteLLMClient("test-key").complete(
        model=MODELS[0], system="sys", user="usr", json_mode=True, timeout_s=60
    )
    assert response.text == '{"ok": true}'
    assert (response.input_tokens, response.output_tokens, response.cost_usd) == (11, 7, 0.0)
    assert seen["num_retries"] == 0
    assert seen["timeout"] == 60
    assert seen["temperature"] == 0.1
    assert seen["max_tokens"] == 1500
    assert seen["extra_headers"] == EXTRA_HEADERS
    assert seen["extra_body"] == EXTRA_BODY
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["messages"][0] == {"role": "system", "content": "sys"}


def test_litellm_client_omits_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_fake_litellm(monkeypatch, None)
    LiteLLMClient("k").complete(model=MODELS[1], system="s", user="u", json_mode=False, timeout_s=5)
    assert "response_format" not in seen


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (_FakeTimeout("t"), "timeout"),
        (_FakeRateLimit("r"), "rate_limited"),
        (RuntimeError("boom"), "provider_error"),
    ],
)
def test_litellm_errors_are_mapped(
    monkeypatch: pytest.MonkeyPatch, error: Exception, outcome: str
) -> None:
    _install_fake_litellm(monkeypatch, error)
    with pytest.raises(LLMError) as info:
        LiteLLMClient("k").complete(model="m", system="s", user="u", json_mode=False, timeout_s=5)
    assert info.value.outcome == outcome
