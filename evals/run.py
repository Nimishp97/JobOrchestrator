"""On-demand eval harness: `uv run python -m evals.run demo_extract_job`.

Runs a JSONL dataset through the real client (needs ANTHROPIC_API_KEY, costs money, counts
toward the daily budget) and prints per-field accuracy, failures, tokens and cost.
Never collected by pytest.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from jobagent.config import AppConfig, Settings
from jobagent.db.__main__ import open_engine
from jobagent.db.engine import create_session_factory
from jobagent.llm import LLMClient, LLMError, load_prompt
from jobagent.llm.anthropic_transport import AnthropicTransport
from jobagent.llm.demo import JobExtraction

EVALS_DIR = Path(__file__).resolve().parent
TEXT_FIELDS = ("company", "title", "location")


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _jaccard(expected: list[str], actual: list[str]) -> float:
    a, b = {_norm(s) for s in expected}, {_norm(s) for s in actual}
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def run_demo_extract_job() -> int:
    settings = Settings()
    if not settings.api_key_set:
        print("ANTHROPIC_API_KEY is not set in .env; evals call the real API.", file=sys.stderr)
        return 1
    engine, label = open_engine()
    client = LLMClient(
        transport=AnthropicTransport(settings.ANTHROPIC_API_KEY.get_secret_value()),  # type: ignore[union-attr]
        config=AppConfig.from_yaml(),
        session_factory=create_session_factory(engine),
    )
    prompt = load_prompt("demo_extract_job", "v1")
    cases = [
        json.loads(line)
        for line in (EVALS_DIR / "demo_extract_job.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    hits = dict.fromkeys(TEXT_FIELDS, 0)
    skill_score = 0.0
    failures: list[str] = []
    input_tokens = output_tokens = 0
    cost, unpriced = Decimal(0), 0
    for case in cases:
        try:
            result = client.run_structured(
                "demo_extract_job", prompt, {"job_description": case["jd"]}, JobExtraction
            )
        except LLMError as exc:
            failures.append(f"{case['id']}: {type(exc).__name__}: {exc}")
            continue
        actual, expected = result.parsed, case["expected"]
        input_tokens += result.usage.input_tokens
        output_tokens += result.usage.output_tokens
        if result.cost_usd is None:
            unpriced += 1
        else:
            cost += result.cost_usd
        for field in TEXT_FIELDS:
            if _norm(getattr(actual, field)) == _norm(expected[field]):
                hits[field] += 1
            else:
                failures.append(
                    f"{case['id']}: {field}: expected {expected[field]!r}, "
                    f"got {getattr(actual, field)!r}"
                )
        skill_score += _jaccard(expected["skills"], actual.skills)

    n = len(cases)
    print(f"demo_extract_job | prompt {prompt.version} {prompt.content_hash[:12]} | db: {label}")
    print(f"cases: {n}")
    for field in TEXT_FIELDS:
        print(f"  {field:<9} accuracy: {hits[field]}/{n}")
    print(f"  skills    mean Jaccard: {skill_score / n:.2f}")
    print(f"tokens: {input_tokens} in / {output_tokens} out | cost: ${cost:.4f}", end="")
    print(f" (+{unpriced} unpriced call(s))" if unpriced else "")
    if failures:
        print("failures:")
        for line in failures:
            print(f"  - {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("dataset", choices=["demo_extract_job"])
    parser.parse_args(argv)
    return run_demo_extract_job()


if __name__ == "__main__":
    sys.exit(main())
