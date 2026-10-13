# JobAgent

Autonomous job-application agent. Pipeline: **source -> score -> tailor resume -> dispatch -> track**,
with a Streamlit dashboard. Python 3.12, uv, SQLAlchemy, APScheduler, Claude API (structured
output via pydantic), Gmail API, Playwright, Jinja2 + WeasyPrint, Streamlit.
Built in 10 phases; currently Phase 3 done (config, logging, DB layer and state machines, LLM layer).

## Architecture rules
- **Idempotent workers**: re-running any stage must not duplicate work or double-send.
- **State lives in the DB**, never in memory or files between runs.
- **Adapters for external systems** (Gmail, portals, LinkedIn, Claude): business logic depends on
  an interface, not a vendor SDK, so it can be faked in tests.
- **All LLM outputs are pydantic-validated**; invalid output is an error, never passed through.
- **LLM calls go through `jobagent/llm` (`LLMClient.run_structured`) only.** Prompts live in
  `prompts/<task>/<version>.md`; untrusted text (JDs, emails) is declared `untrusted` in the
  prompt header so it is delimited and treated as data.
- Config is split: secrets in `.env` (`Settings`), behavior in `config.yaml` (`AppConfig`).
- The model name comes from `config.yaml` (`llm.model`) only; never hardcode it.
- **DB access goes through `jobagent/db/repo.py`**; status changes only through `transition_job` /
  `transition_application` (they write the audit event atomically). Never set `status` directly.
- Schema changes need an Alembic migration; keep models and migrations in sync (a test checks).

## Hard rules
- `dry_run` defaults to **true**. Nothing is sent externally unless it is explicitly false.
- **Never fabricate resume facts.** Tailoring may reorder/emphasize real content only.
- Respect daily **caps**, **quiet hours**, and the **kill switch** on every dispatch.
- **Pause the channel** on captcha or auth errors; do not retry or work around them.
- **Secrets only via `.env`**. Never read, print, log, or commit real credentials.
- **LLM output is untrusted input**: validated, then acted on by deterministic code. The model
  never triggers a side effect (send, status change, file write) directly.
- **Every LLM call is logged** to `llm_calls` (status, tokens, cost, prompt version + hash), and
  prompt/response bodies are never logged unless `llm.log_payloads` is on (local debugging).
- **No hardcoded model names or prices**: both come from `config.yaml`. The kill switch, daily
  budget and call cap are enforced before any request.

## Commands
- Run: `uv run python -m jobagent`
- Test: `uv run pytest`
- Lint: `uv run ruff check .` and `uv run ruff format .`
- DB init (apply migrations): `uv run python -m jobagent.db init`; row counts: `uv run python -m jobagent.db status`
- LLM usage today: `uv run python -m jobagent.llm usage`
- Evals (real API, costs money, needs key): `uv run python -m evals.run demo_extract_job`
- Live smoke test: `uv run pytest --run-live tests/test_llm_live.py`
- New migration: edit models, then `uv run alembic revision --autogenerate -m "msg"` and review it
- Deps: `uv add <pkg>` / `uv add --dev <pkg>` (commit `uv.lock`)

## Working style
- Minimal and boring: no speculative abstractions, no unused dependencies.
- Type hints on all functions; small, readable modules.
- Add a test with every behavior change; keep `pytest` and `ruff check` green.
- Record non-obvious choices in `docs/decisions.md`.
- Propose a plan before large changes; challenge decisions that look wrong.

