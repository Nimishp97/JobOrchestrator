"""`python -m jobagent.llm usage`: today's LLM calls, tokens, cost and remaining budget."""

import argparse
import sys

from jobagent.config import AppConfig
from jobagent.db import repo
from jobagent.db.__main__ import open_engine
from jobagent.db.engine import create_session_factory, session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobagent.llm")
    parser.add_argument("command", choices=["usage"])
    parser.parse_args(argv)

    llm = AppConfig.from_yaml().llm
    engine, label = open_engine()
    with session_scope(create_session_factory(engine)) as session:
        usage = repo.llm_usage_today(session)

    spent = usage["cost_usd"]
    print(f"LLM usage today (UTC) | database: {label}")
    print(f"  calls:         {usage['calls']} / {llm.max_calls_per_day}")
    print(f"  input tokens:  {usage['input_tokens']}")
    print(f"  output tokens: {usage['output_tokens']}")
    print(f"  cost (USD):    {spent:.4f} / {llm.daily_budget_usd:.2f}")
    print(f"  remaining:     {max(llm.daily_budget_usd - float(spent), 0):.4f} USD")
    if usage["unpriced_calls"]:
        print(f"  note: {usage['unpriced_calls']} call(s) had no pricing; cost is a lower bound")
    return 0


if __name__ == "__main__":
    sys.exit(main())


"""" knjlnkj """
