"""Cost from token usage and the config pricing table. Unknown price means unknown cost."""

from collections.abc import Mapping
from decimal import Decimal

from jobagent.config import ModelPrice

_MILLION = Decimal(1_000_000)


def compute_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    pricing: Mapping[str, ModelPrice],
) -> Decimal | None:
    """USD cost, or None when the model (or a needed cache price) is not in `pricing`."""
    price = pricing.get(model)
    if price is None:
        return None
    if (cache_read_tokens and price.cache_read is None) or (
        cache_write_tokens and price.cache_write is None
    ):
        return None
    total = (
        Decimal(str(price.input)) * input_tokens
        + Decimal(str(price.output)) * output_tokens
        + Decimal(str(price.cache_read or 0)) * cache_read_tokens
        + Decimal(str(price.cache_write or 0)) * cache_write_tokens
    )
    return total / _MILLION
