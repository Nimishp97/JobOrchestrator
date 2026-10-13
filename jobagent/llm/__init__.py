"""LLM layer: structured, validated, logged model calls. See docs/decisions.md (009-014)."""

from jobagent.llm.client import LLMClient, LLMResult, Usage
from jobagent.llm.errors import (
    LLMBudgetExceeded,
    LLMError,
    LLMKillSwitchError,
    LLMOutputError,
    LLMRefusalError,
    LLMTransportError,
)
from jobagent.llm.prompts import PromptError, PromptTemplate, load_prompt
from jobagent.llm.untrusted import wrap_untrusted

__all__ = [
    "LLMBudgetExceeded",
    "LLMClient",
    "LLMError",
    "LLMKillSwitchError",
    "LLMOutputError",
    "LLMRefusalError",
    "LLMResult",
    "LLMTransportError",
    "PromptError",
    "PromptTemplate",
    "Usage",
    "load_prompt",
    "wrap_untrusted",
]
