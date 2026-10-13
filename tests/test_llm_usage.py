from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from jobagent.config import ModelPrice
from jobagent.db import repo
from jobagent.db.models import LLMCallStatus, utcnow
from jobagent.llm.cost import compute_cost

PRICES = {"m": ModelPrice(input=2.0, output=10.0, cache_read=0.1)}


def add(session: Session, **kw) -> None:
    repo.record_llm_call(
        session,
        task="t",
        model="m",
        prompt_version="v1",
        prompt_hash="h",
        status=kw.pop("status", LLMCallStatus.OK),
        **kw,
    )


def test_usage_counts_only_todays_real_requests(session: Session) -> None:
    add(session, attempts=1, input_tokens=10, output_tokens=5, cost_usd=Decimal("0.5"))
    add(session, attempts=2, input_tokens=1, output_tokens=1, cost_usd=None)  # unpriced
    add(session, attempts=0, status=LLMCallStatus.BUDGET_BLOCKED)  # no request was made
    add(session, attempts=1, cost_usd=Decimal("9"), now=utcnow() - timedelta(days=1))

    usage = repo.llm_usage_today(session)
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 11 and usage["output_tokens"] == 6
    assert usage["cost_usd"] == Decimal("0.5")
    assert usage["unpriced_calls"] == 1


def test_cost_math() -> None:
    cost = compute_cost(
        "m",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=0,
        pricing=PRICES,
    )
    assert cost == Decimal("7.1")  # 2 + 5 + 0.1


def test_cost_is_none_for_unknown_model_or_missing_cache_price() -> None:
    kwargs = {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0, "pricing": PRICES}
    assert compute_cost("unknown", cache_write_tokens=0, **kwargs) is None
    assert compute_cost("m", cache_write_tokens=5, **kwargs) is None  # no cache_write price
