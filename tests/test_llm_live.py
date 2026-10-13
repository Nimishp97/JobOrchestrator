"""Live smoke test: real API, real key. Skipped unless run with `--run-live`."""

import pytest

from jobagent.config import AppConfig, Settings
from jobagent.db.engine import create_session_factory
from jobagent.llm import LLMClient, load_prompt
from jobagent.llm.anthropic_transport import AnthropicTransport
from jobagent.llm.demo import JobExtraction

pytestmark = pytest.mark.live

SAMPLE_JD = (
    "Northwind Traders is hiring a Backend Engineer in Bengaluru, India. "
    "You will build APIs with Python and PostgreSQL. Experience with Docker is required."
)


def test_extracts_fields_from_a_sample_jd(engine) -> None:
    settings = Settings()
    assert settings.api_key_set, "set ANTHROPIC_API_KEY in .env to run live tests"
    client = LLMClient(
        transport=AnthropicTransport(settings.ANTHROPIC_API_KEY.get_secret_value()),  # type: ignore[union-attr]
        config=AppConfig.from_yaml(),
        session_factory=create_session_factory(engine),  # throwaway DB, not your real one
    )
    result = client.run_structured(
        "demo_extract_job",
        load_prompt("demo_extract_job", "v1"),
        {"job_description": SAMPLE_JD},
        JobExtraction,
    )
    assert "northwind" in result.parsed.company.lower()
    assert "bengaluru" in result.parsed.location.lower()
    assert result.usage.input_tokens > 0 and result.prompt_version == "v1"
