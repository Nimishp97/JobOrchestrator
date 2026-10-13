"""LLMClient.run_structured: the single entry point for model calls.

Two separate failure layers:
  1. transport (429, 5xx, timeouts, connection): backoff + jitter, bounded; 4xx fails fast.
  2. validation (model answered, pydantic rejected it): bounded repair retries that feed the
     validation error back to the model, then LLMOutputError. Nothing is coerced or partial.
The model returns data only; callers act on the validated object. Every call writes one
llm_calls row (never prompt/response text, unless `llm.log_payloads` is on).
"""

import json
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from jobagent.config import PROJECT_ROOT, AppConfig
from jobagent.db import repo
from jobagent.db.engine import session_scope
from jobagent.db.models import LLMCallStatus, utcnow
from jobagent.llm.cost import compute_cost
from jobagent.llm.errors import (
    LLMBudgetExceeded,
    LLMError,
    LLMKillSwitchError,
    LLMOutputError,
    LLMRefusalError,
    LLMTransportError,
)
from jobagent.llm.prompts import PromptTemplate
from jobagent.llm.transport import (
    Message,
    Transport,
    TransportError,
    TransportRequest,
    TransportResponse,
)

logger = logging.getLogger(__name__)
DEBUG_DIR = PROJECT_ROOT / "data" / "llm_debug"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    parsed: T
    usage: Usage
    latency_ms: int
    model: str
    prompt_version: str
    prompt_hash: str
    attempts: int  # requests sent: transport retries and validation repairs both count
    stop_reason: str | None
    cost_usd: Decimal | None


@dataclass
class _Tally:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, response: TransportResponse) -> None:
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.cache_read_tokens += response.cache_read_tokens
        self.cache_write_tokens += response.cache_write_tokens

    def usage(self) -> Usage:
        return Usage(
            self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens
        )


def _error_type(exc: LLMError) -> str:
    if isinstance(exc, LLMOutputError):
        return "truncated" if exc.truncated else "invalid_output"
    if isinstance(exc, LLMTransportError):
        return f"http_{exc.status_code}" if exc.status_code else "connection"
    return type(exc).__name__


def _status(exc: LLMError) -> LLMCallStatus:
    if isinstance(exc, LLMOutputError):
        return LLMCallStatus.VALIDATION_FAILED
    if isinstance(exc, LLMTransportError):
        return LLMCallStatus.TRANSPORT_FAILED
    if isinstance(exc, LLMBudgetExceeded):
        return LLMCallStatus.BUDGET_BLOCKED
    return LLMCallStatus.REFUSED  # model refusal, empty response, kill switch


def _repair_message(error: ValidationError) -> str:
    lines = [
        f"- {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
        for e in error.errors(include_input=False, include_url=False)
    ]
    return (
        "Your previous response did not satisfy the required schema:\n"
        + "\n".join(lines)
        + "\nReturn a corrected response that satisfies the schema."
    )


class LLMClient:
    def __init__(
        self,
        *,
        transport: Transport,
        config: AppConfig,
        session_factory: sessionmaker[Session],
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        debug_dir: Path = DEBUG_DIR,
    ) -> None:
        self._transport = transport
        self._config = config
        self._factory = session_factory
        self._sleep = sleep
        self._jitter = jitter
        self._debug_dir = debug_dir

    # --- public ---------------------------------------------------------------------------

    def run_structured[T: BaseModel](
        self,
        task: str,
        prompt: PromptTemplate,
        variables: Mapping[str, str],
        schema: type[T],
        *,
        job_id: int | None = None,
        application_id: int | None = None,
    ) -> LLMResult[T]:
        """Render the prompt, call the model, return a validated `schema` instance.

        Raises PromptError for bad variables (a programming error, before anything is sent);
        every other failure is an LLMError subclass and leaves one llm_calls row.
        """
        llm = self._config.llm
        settings = llm.for_task(task)
        rendered = prompt.render(variables)
        meta: dict[str, Any] = {
            "task": task,
            "model": settings.model,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
            "job_id": job_id,
            "application_id": application_id,
        }
        tally = _Tally()
        started = time.perf_counter()

        def elapsed_ms() -> int:
            return int((time.perf_counter() - started) * 1000)

        try:
            self._guard()
            request = TransportRequest(
                model=settings.model,
                system=rendered.system,
                messages=(Message("user", rendered.user),),
                schema=schema.model_json_schema(),
                max_tokens=settings.max_tokens,
                timeout_seconds=settings.timeout_seconds,
                temperature=settings.temperature,
            )
            raw = ""
            for _ in range(1 + llm.max_validation_retries):
                response = self._send(request, tally)
                raw = response.text
                self._dump(task, request, response)
                if response.stop_reason == "max_tokens":
                    raise LLMOutputError(
                        "output truncated at max_tokens", raw_output=raw, truncated=True
                    )
                if response.stop_reason == "refusal" or not raw.strip():
                    raise LLMRefusalError(
                        "model refused" if response.stop_reason == "refusal" else "empty response"
                    )
                try:
                    parsed = schema.model_validate_json(raw)
                except ValidationError as exc:
                    request = replace(
                        request,
                        messages=(
                            *request.messages,
                            Message("assistant", raw),
                            Message("user", _repair_message(exc)),
                        ),
                    )
                    continue
                cost = self._cost(settings.model, tally)
                self._record(meta, LLMCallStatus.OK, tally, elapsed_ms(), cost, None)
                return LLMResult(
                    parsed=parsed,
                    usage=tally.usage(),
                    latency_ms=elapsed_ms(),
                    model=settings.model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt.content_hash,
                    attempts=tally.attempts,
                    stop_reason=response.stop_reason,
                    cost_usd=cost,
                )
            raise LLMOutputError(
                f"output failed validation after {1 + llm.max_validation_retries} attempts",
                raw_output=raw,
            )
        except LLMError as exc:
            cost = self._cost(settings.model, tally) if tally.input_tokens else None
            self._record(meta, _status(exc), tally, elapsed_ms(), cost, _error_type(exc))
            raise

    # --- internals ------------------------------------------------------------------------

    def _guard(self) -> None:
        """Kill switch and budget, checked before any network request."""
        if self._config.kill_switch:
            raise LLMKillSwitchError("kill_switch is on: LLM calls are refused")
        llm = self._config.llm
        with session_scope(self._factory) as session:
            usage = repo.llm_usage_today(session)
        if usage["calls"] >= llm.max_calls_per_day:
            raise LLMBudgetExceeded(f"max_calls_per_day reached ({llm.max_calls_per_day})")
        if usage["cost_usd"] >= Decimal(str(llm.daily_budget_usd)):
            raise LLMBudgetExceeded(f"daily_budget_usd reached ({llm.daily_budget_usd})")

    def _send(self, request: TransportRequest, tally: _Tally) -> TransportResponse:
        """One logical request with transport retries. Validation is not this layer's concern."""
        llm = self._config.llm
        for attempt in range(1, llm.max_transport_attempts + 1):
            tally.attempts += 1
            try:
                response = self._transport.send(request)
            except TransportError as exc:
                if not exc.retryable:
                    raise LLMTransportError(str(exc), status_code=exc.status_code) from exc
                delay = self._delay(attempt, exc.retry_after)
                if attempt == llm.max_transport_attempts or delay is None:
                    raise LLMTransportError(
                        f"{exc} (gave up after {attempt} attempt(s))", status_code=exc.status_code
                    ) from exc
                logger.warning("LLM transport error (%s); retrying in %.1fs", exc, delay)
                self._sleep(delay)
            else:
                tally.add(response)
                return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _delay(self, attempt: int, retry_after: float | None) -> float | None:
        """Seconds to wait before the next try, or None when the server asks for too long."""
        llm = self._config.llm
        if retry_after is not None:
            return retry_after if retry_after <= llm.backoff_max_seconds else None
        base = min(llm.backoff_max_seconds, llm.backoff_base_seconds * 2 ** (attempt - 1))
        return base * (0.5 + 0.5 * self._jitter())

    def _cost(self, model: str, tally: _Tally) -> Decimal | None:
        cost = compute_cost(
            model,
            input_tokens=tally.input_tokens,
            output_tokens=tally.output_tokens,
            cache_read_tokens=tally.cache_read_tokens,
            cache_write_tokens=tally.cache_write_tokens,
            pricing=self._config.llm.pricing,
        )
        if cost is None:
            logger.warning("no pricing for model %r: cost_usd recorded as NULL", model)
        return cost

    def _record(
        self,
        meta: dict[str, Any],
        status: LLMCallStatus,
        tally: _Tally,
        latency_ms: int,
        cost: Decimal | None,
        error_type: str | None,
    ) -> None:
        with session_scope(self._factory) as session:
            repo.record_llm_call(
                session,
                **meta,
                status=status,
                attempts=tally.attempts,
                input_tokens=tally.input_tokens,
                output_tokens=tally.output_tokens,
                cache_read_tokens=tally.cache_read_tokens,
                cache_write_tokens=tally.cache_write_tokens,
                latency_ms=latency_ms,
                error_type=error_type,
                cost_usd=cost,
            )

    def _dump(self, task: str, request: TransportRequest, response: TransportResponse) -> None:
        """Local debugging only: contains personal data, so off unless `llm.log_payloads`."""
        if not self._config.llm.log_payloads:
            return
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        path = self._debug_dir / f"{utcnow():%Y%m%dT%H%M%S%f}_{task}.json"
        payload = {
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "response": response.text,
            "stop_reason": response.stop_reason,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
