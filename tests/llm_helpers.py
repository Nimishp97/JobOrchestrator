"""Test doubles for the LLM layer. No network, no API key."""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from jobagent.config import AppConfig
from jobagent.llm.client import LLMClient
from jobagent.llm.prompts import PromptTemplate, load_prompt
from jobagent.llm.transport import TransportError, TransportRequest, TransportResponse

FAKE_MODEL = "fake-model"
VALID_JSON = '{"company": "Acme", "title": "Engineer", "location": "Pune", "skills": ["Python"]}'
PRICING = {FAKE_MODEL: {"input": 2.0, "output": 10.0, "cache_read": 0.1, "cache_write": 2.5}}


def ok(
    text: str = VALID_JSON,
    *,
    stop_reason: str | None = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> TransportResponse:
    return TransportResponse(
        text=text,
        stop_reason=stop_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def http_error(
    status: int, *, retryable: bool | None = None, retry_after: float | None = None
) -> TransportError:
    return TransportError(
        f"HTTP {status}",
        retryable=status == 429 or status >= 500 if retryable is None else retryable,
        status_code=status,
        retry_after=retry_after,
    )


class FakeTransport:
    """Returns scripted responses in order; raises scripted TransportErrors."""

    def __init__(self, script: Sequence[TransportResponse | TransportError]) -> None:
        self._script = list(script)
        self.requests: list[TransportRequest] = []

    def send(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("FakeTransport script exhausted")
        item = self._script.pop(0)
        if isinstance(item, TransportError):
            raise item
        return item


def make_config(**llm_overrides: object) -> AppConfig:
    llm = {"model": FAKE_MODEL, "pricing": PRICING, **llm_overrides}
    return AppConfig.model_validate({"llm": llm})


def make_client(
    transport: FakeTransport,
    factory: sessionmaker[Session],
    *,
    config: AppConfig | None = None,
    sleeps: list[float] | None = None,
    debug_dir: Path | None = None,
) -> LLMClient:
    recorded = sleeps if sleeps is not None else []
    kwargs = {"debug_dir": debug_dir} if debug_dir is not None else {}
    return LLMClient(
        transport=transport,
        config=config or make_config(),
        session_factory=factory,
        sleep=recorded.append,
        jitter=lambda: 1.0,  # deterministic: full computed delay
        **kwargs,  # type: ignore[arg-type]
    )


def demo_prompt() -> PromptTemplate:
    return load_prompt("demo_extract_job", "v1")
