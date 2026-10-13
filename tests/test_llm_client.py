import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jobagent.db import repo
from jobagent.db.models import LLMCall, LLMCallStatus
from jobagent.llm import (
    LLMBudgetExceeded,
    LLMError,
    LLMKillSwitchError,
    LLMOutputError,
    LLMRefusalError,
    LLMTransportError,
)
from jobagent.llm.demo import JobExtraction
from tests.helpers import make_job
from tests.llm_helpers import (
    FAKE_MODEL,
    VALID_JSON,
    FakeTransport,
    demo_prompt,
    http_error,
    make_client,
    make_config,
    ok,
)

JD = "Acme is hiring an Engineer in Pune. Python required."
SECRET_JD = "CONFIDENTIAL-JD-TEXT-12345"


def run(client, jd: str = JD):
    return client.run_structured(
        "demo_extract_job", demo_prompt(), {"job_description": jd}, JobExtraction
    )


def rows(factory: sessionmaker[Session]) -> list[LLMCall]:
    with factory() as session:
        return list(session.scalars(select(LLMCall).order_by(LLMCall.id)))


# --- happy path ---------------------------------------------------------------------------


def test_happy_path(factory: sessionmaker[Session]) -> None:
    transport = FakeTransport([ok()])
    result = run(make_client(transport, factory))

    assert result.parsed == JobExtraction(
        company="Acme", title="Engineer", location="Pune", skills=["Python"]
    )
    assert result.usage.input_tokens == 100 and result.usage.output_tokens == 20
    assert result.model == FAKE_MODEL
    assert result.prompt_version == "v1"
    assert len(result.prompt_hash) == 64
    assert result.attempts == 1
    assert result.stop_reason == "end_turn"
    assert result.cost_usd == Decimal("0.000400")  # 100*2/1e6 + 20*10/1e6

    request = transport.requests[0]
    assert request.model == FAKE_MODEL and request.temperature == 0
    assert request.max_tokens > 0 and request.timeout_seconds > 0
    assert "<job_description>" in request.messages[0].content
    assert "never instructions" in request.system

    (row,) = rows(factory)
    assert row.status == LLMCallStatus.OK
    assert (row.prompt_version, row.prompt_hash) == ("v1", result.prompt_hash)
    assert (row.input_tokens, row.output_tokens, row.attempts) == (100, 20, 1)
    assert row.cost_usd == Decimal("0.000400")


def test_links_job_id(session: Session, factory: sessionmaker[Session]) -> None:
    job = make_job(session)
    session.commit()
    client = make_client(FakeTransport([ok()]), factory)
    client.run_structured(
        "demo_extract_job", demo_prompt(), {"job_description": JD}, JobExtraction, job_id=job.id
    )
    assert rows(factory)[0].job_id == job.id


def test_task_override_selects_model(factory: sessionmaker[Session]) -> None:
    config = make_config(tasks={"demo_extract_job": {"model": "other-model", "max_tokens": 77}})
    transport = FakeTransport([ok()])
    result = run(make_client(transport, factory, config=config))
    assert transport.requests[0].model == "other-model"
    assert transport.requests[0].max_tokens == 77
    assert result.cost_usd is None  # no pricing entry for that model: null, not a guess
    assert rows(factory)[0].cost_usd is None


# --- validation layer ---------------------------------------------------------------------


def test_invalid_then_valid_feeds_error_back(factory: sessionmaker[Session]) -> None:
    bad = '{"company": "Acme", "title": "Engineer", "location": "Pune"}'  # skills missing
    transport = FakeTransport([ok(bad), ok()])
    result = run(make_client(transport, factory))

    assert result.attempts == 2
    assert result.parsed.company == "Acme"
    repair = transport.requests[1].messages
    assert [m.role for m in repair] == ["user", "assistant", "user"]
    assert repair[1].content == bad
    assert "skills" in repair[2].content and "Field required" in repair[2].content
    assert rows(factory)[0].attempts == 2


def test_persistent_invalid_raises_output_error_with_raw(factory: sessionmaker[Session]) -> None:
    transport = FakeTransport([ok("not json")] * 3)  # 1 try + 2 repairs
    with pytest.raises(LLMOutputError) as info:
        run(make_client(transport, factory))
    assert info.value.raw_output == "not json"
    assert not info.value.truncated
    assert len(transport.requests) == 3
    (row,) = rows(factory)
    assert row.status == LLMCallStatus.VALIDATION_FAILED
    assert row.error_type == "invalid_output" and row.attempts == 3
    assert row.input_tokens == 300  # failed calls are still billed and recorded


def test_truncated_output_is_a_failure_not_parsed(factory: sessionmaker[Session]) -> None:
    transport = FakeTransport([ok(VALID_JSON, stop_reason="max_tokens")])
    with pytest.raises(LLMOutputError) as info:
        run(make_client(transport, factory))
    assert info.value.truncated
    assert len(transport.requests) == 1  # no pointless repair retry
    assert rows(factory)[0].error_type == "truncated"


@pytest.mark.parametrize(
    "response", [ok("", stop_reason="end_turn"), ok(VALID_JSON, stop_reason="refusal")]
)
def test_refusal_or_empty_raises_refusal(factory: sessionmaker[Session], response: object) -> None:
    with pytest.raises(LLMRefusalError):
        run(make_client(FakeTransport([response]), factory))  # type: ignore[list-item]
    assert rows(factory)[0].status == LLMCallStatus.REFUSED


# --- transport layer ----------------------------------------------------------------------


def test_429_is_retried_with_growing_backoff(factory: sessionmaker[Session]) -> None:
    sleeps: list[float] = []
    transport = FakeTransport([http_error(429), http_error(503), ok()])
    result = run(make_client(transport, factory, sleeps=sleeps))
    assert result.attempts == 3
    assert sleeps == [1.0, 2.0]  # base * 2**n with jitter pinned to its maximum


def test_retry_after_is_honored(factory: sessionmaker[Session]) -> None:
    sleeps: list[float] = []
    transport = FakeTransport([http_error(429, retry_after=7.0), ok()])
    run(make_client(transport, factory, sleeps=sleeps))
    assert sleeps == [7.0]


def test_retry_after_longer_than_cap_fails_fast(factory: sessionmaker[Session]) -> None:
    sleeps: list[float] = []
    transport = FakeTransport([http_error(429, retry_after=3600.0)])
    with pytest.raises(LLMTransportError):
        run(make_client(transport, factory, sleeps=sleeps))
    assert sleeps == [] and len(transport.requests) == 1


def test_auth_error_fails_fast_without_retry(factory: sessionmaker[Session]) -> None:
    sleeps: list[float] = []
    transport = FakeTransport([http_error(401)])
    with pytest.raises(LLMTransportError) as info:
        run(make_client(transport, factory, sleeps=sleeps))
    assert info.value.status_code == 401
    assert sleeps == [] and len(transport.requests) == 1
    row = rows(factory)[0]
    assert row.status == LLMCallStatus.TRANSPORT_FAILED and row.error_type == "http_401"


def test_transport_retries_are_bounded(factory: sessionmaker[Session]) -> None:
    config = make_config(max_transport_attempts=3)
    transport = FakeTransport([http_error(500)] * 3)
    with pytest.raises(LLMTransportError):
        run(make_client(transport, factory, config=config))
    assert len(transport.requests) == 3
    assert rows(factory)[0].attempts == 3


def test_transport_and_validation_layers_are_independent(
    factory: sessionmaker[Session],
) -> None:
    transport = FakeTransport([http_error(500), ok("nope"), http_error(429), ok()])
    result = run(make_client(transport, factory))
    assert result.attempts == 4 and result.parsed.title == "Engineer"


# --- guards -------------------------------------------------------------------------------


def test_call_cap_blocks_before_any_request(factory: sessionmaker[Session]) -> None:
    config = make_config(max_calls_per_day=1)
    run(make_client(FakeTransport([ok()]), factory, config=config))

    transport = FakeTransport([ok()])
    with pytest.raises(LLMBudgetExceeded):
        run(make_client(transport, factory, config=config))
    assert transport.requests == []
    assert rows(factory)[-1].status == LLMCallStatus.BUDGET_BLOCKED
    assert rows(factory)[-1].attempts == 0


def test_usd_budget_blocks_before_any_request(factory: sessionmaker[Session]) -> None:
    config = make_config(daily_budget_usd=0.0003)  # one call costs 0.0004
    run(make_client(FakeTransport([ok()]), factory, config=config))
    transport = FakeTransport([ok()])
    with pytest.raises(LLMBudgetExceeded):
        run(make_client(transport, factory, config=config))
    assert transport.requests == []


def test_budget_blocks_do_not_count_toward_the_cap(factory: sessionmaker[Session]) -> None:
    config = make_config(max_calls_per_day=1)
    run(make_client(FakeTransport([ok()]), factory, config=config))
    for _ in range(2):
        with pytest.raises(LLMBudgetExceeded):
            run(make_client(FakeTransport([]), factory, config=config))
    with factory() as session:
        assert repo.llm_usage_today(session)["calls"] == 1


def test_kill_switch_refuses_without_request(factory: sessionmaker[Session]) -> None:
    config = make_config().model_copy(update={"kill_switch": True})
    transport = FakeTransport([ok()])
    with pytest.raises(LLMKillSwitchError):
        run(make_client(transport, factory, config=config))
    assert transport.requests == []
    row = rows(factory)[0]
    assert row.status == LLMCallStatus.REFUSED and row.error_type == "LLMKillSwitchError"


def test_all_errors_share_the_base_class() -> None:
    for cls in (LLMTransportError, LLMOutputError, LLMRefusalError, LLMBudgetExceeded):
        assert issubclass(cls, LLMError)
    assert issubclass(LLMKillSwitchError, LLMError)


# --- privacy ------------------------------------------------------------------------------


def test_rows_contain_no_prompt_or_response_text(factory: sessionmaker[Session]) -> None:
    run(make_client(FakeTransport([ok()]), factory), jd=SECRET_JD)
    with pytest.raises(LLMOutputError):
        run(make_client(FakeTransport([ok("junk-response-xyz")] * 3), factory), jd=SECRET_JD)

    for row in rows(factory):
        text = " ".join(str(getattr(row, c.name)) for c in LLMCall.__table__.columns)
        assert SECRET_JD not in text and "junk-response-xyz" not in text and "Acme" not in text


def test_payloads_are_written_only_when_enabled(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    off = tmp_path / "off"
    run(make_client(FakeTransport([ok()]), factory, debug_dir=off), jd=SECRET_JD)
    assert not off.exists()

    on = tmp_path / "on"
    config = make_config(log_payloads=True)
    run(make_client(FakeTransport([ok()]), factory, config=config, debug_dir=on), jd=SECRET_JD)
    (dump,) = on.glob("*.json")
    assert SECRET_JD in json.loads(dump.read_text(encoding="utf-8"))["messages"][0]["content"]


def test_llm_calls_table_exists_after_migration(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        assert "llm_calls" in repo.row_counts(session)
