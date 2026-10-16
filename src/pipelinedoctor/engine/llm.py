"""LLM access: a tiny client protocol, the LiteLLM implementation, a request budget, and fallback.

Every request is counted. LiteLLM's own retries are switched off (`num_retries=0`) because hidden
retries would silently burn the OpenRouter free-tier quota (~50 requests/day, ~20/minute).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pipelinedoctor.schemas import AttemptOutcome, LLMAttempt

log = logging.getLogger(__name__)

T = TypeVar("T")

EXTRA_HEADERS = {
    "HTTP-Referer": "https://github.com/gagan046-dev/pipelinedoctor",
    "X-Title": "PipelineDoctor",
}
# Nemotron 3 models reason before answering; keep it short and out of the returned text.
EXTRA_BODY = {"reasoning": {"effort": "low", "exclude": True}}


@dataclass(frozen=True)
class ModelProfile:
    json_mode: bool


# Verified against OpenRouter's /api/v1/models "supported_parameters" (2026-10-16).
MODEL_PROFILES = {
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free": ModelProfile(json_mode=True),
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": ModelProfile(json_mode=False),
}


def profile_for(model: str) -> ModelProfile:
    # Unknown models: prompt-only JSON is the safe default (extract_json copes with prose).
    return MODEL_PROFILES.get(model, ModelProfile(json_mode=False))


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LLMError(Exception):
    def __init__(self, outcome: AttemptOutcome, message: str) -> None:
        super().__init__(message)
        self.outcome: AttemptOutcome = outcome


class LLMClient(Protocol):
    def complete(
        self, *, model: str, system: str, user: str, json_mode: bool, timeout_s: float
    ) -> LLMResponse: ...


class LiteLLMClient:
    """OpenAI-compatible calls via the LiteLLM SDK (imported lazily: slow import; tests skip it)."""

    def __init__(self, api_key: str, temperature: float = 0.1, max_tokens: int = 1500) -> None:
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens

    def complete(
        self, *, model: str, system: str, user: str, json_mode: bool, timeout_s: float
    ) -> LLMResponse:
        import litellm

        litellm.suppress_debug_info = True
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)  # it logs every call at INFO
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "api_key": self._api_key,
            "timeout": timeout_s,
            "num_retries": 0,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "extra_headers": EXTRA_HEADERS,
            "extra_body": EXTRA_BODY,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        try:
            response = litellm.completion(**kwargs)
        except litellm.exceptions.Timeout as exc:
            raise LLMError("timeout", type(exc).__name__) from exc
        except litellm.exceptions.RateLimitError as exc:
            raise LLMError("rate_limited", type(exc).__name__) from exc
        except Exception as exc:  # any provider/network error is just "this model failed"
            raise LLMError("provider_error", f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        try:
            cost = float(litellm.completion_cost(completion_response=response) or 0.0)
        except Exception:  # free models are often missing from LiteLLM's price map
            cost = 0.0
        return LLMResponse(
            text=text,
            model=model,
            latency_ms=latency_ms,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_usd=cost,
        )


class RequestBudget:
    """Process-wide cap on LLM requests plus minimum spacing between them.

    It is also a small circuit breaker: once a model answers 429, it is skipped for
    `rate_limit_cooldown_s`. On a ~50 requests/day free tier, asking a model that just said
    "rate limited" again for every eval case would burn half the budget for nothing.
    """

    def __init__(
        self,
        max_requests: int,
        min_interval_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rate_limit_cooldown_s: float = 60.0,
    ) -> None:
        self.max_requests = max_requests
        self.min_interval_s = min_interval_s
        self.rate_limit_cooldown_s = rate_limit_cooldown_s
        self.used = 0
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._limited_until: dict[str, float] = {}

    def mark_rate_limited(self, model: str) -> None:
        self._limited_until[model] = self._clock() + self.rate_limit_cooldown_s

    def is_rate_limited(self, model: str) -> bool:
        return self._clock() < self._limited_until.get(model, float("-inf"))

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used)

    def acquire(self) -> bool:
        if self.used >= self.max_requests:
            return False
        if self._last is not None:
            wait = self.min_interval_s - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        self.used += 1
        return True


@dataclass
class FallbackResult(Generic[T]):
    value: T | None = None
    response: LLMResponse | None = None
    attempts: list[LLMAttempt] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(1 for a in self.attempts if a.outcome != "budget_exhausted")

    @property
    def latency_ms(self) -> int:
        return sum(a.latency_ms for a in self.attempts)


def call_with_fallback(
    client: LLMClient,
    models: list[str],
    system: str,
    user: str,
    budget: RequestBudget,
    timeout_s: float,
    parse: Callable[[str], T],
) -> FallbackResult[T]:
    """Try each model once, in order. `parse` raises ValueError when the answer is unusable."""
    result: FallbackResult[T] = FallbackResult()
    for model in models:
        if budget.is_rate_limited(model):
            # Not an attempt (no request is sent), so it is only noted, never counted as a call.
            result.errors.append(f"{model}: skipped, rate limited earlier in this run")
            continue
        if not budget.acquire():
            result.attempts.append(LLMAttempt(model=model, outcome="budget_exhausted"))
            result.errors.append(f"{model}: request budget exhausted")
            break
        started = time.monotonic()
        try:
            response = client.complete(
                model=model,
                system=system,
                user=user,
                json_mode=profile_for(model).json_mode,
                timeout_s=timeout_s,
            )
        except LLMError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            result.attempts.append(LLMAttempt(model=model, outcome=exc.outcome, latency_ms=elapsed))
            result.errors.append(f"{model}: {exc.outcome} ({exc})")
            log.warning("LLM %s failed: %s", model, exc.outcome)
            if exc.outcome == "rate_limited":
                budget.mark_rate_limited(model)
            continue
        try:
            value = parse(response.text)
        except ValueError as exc:
            result.attempts.append(
                LLMAttempt(model=model, outcome="invalid_json", latency_ms=response.latency_ms)
            )
            result.errors.append(f"{model}: invalid_json ({str(exc)[:200]})")
            log.warning("LLM %s returned unusable output", model)
            continue
        result.attempts.append(
            LLMAttempt(model=model, outcome="ok", latency_ms=response.latency_ms)
        )
        result.value = value
        result.response = response
        return result
    return result
