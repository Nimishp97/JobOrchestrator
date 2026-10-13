"""Typed LLM failures. Callers catch the subclass they can act on, or LLMError for any."""


class LLMError(Exception):
    """Base class for every failure raised by the LLM layer."""


class LLMTransportError(LLMError):
    """The request could not be completed (after retries, or a non-retryable 4xx)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMOutputError(LLMError):
    """The model answered, but not with usable output (invalid, or truncated)."""

    def __init__(self, message: str, *, raw_output: str = "", truncated: bool = False) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.truncated = truncated


class LLMRefusalError(LLMError):
    """The model refused, or returned no content."""


class LLMBudgetExceeded(LLMError):
    """The daily USD budget or call cap is used up. No request was made."""


class LLMKillSwitchError(LLMError):
    """`kill_switch` is on in config. No request was made."""
