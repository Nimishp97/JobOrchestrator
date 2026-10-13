"""The one seam between the client and the network. Tests pass a fake that implements it."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class TransportRequest:
    model: str
    system: str
    messages: tuple[Message, ...]
    schema: dict[str, Any]  # JSON schema for the structured output
    max_tokens: int
    timeout_seconds: float
    temperature: float | None


@dataclass(frozen=True)
class TransportResponse:
    text: str  # concatenated text blocks ("" when there are none)
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class TransportError(Exception):
    """A failed request, already classified. `retryable` is True for 429, 5xx, timeouts and
    connection errors; auth and validation errors (other 4xx) are not."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after


class Transport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse:
        """Perform one request. Raises TransportError; never retries."""
        ...
