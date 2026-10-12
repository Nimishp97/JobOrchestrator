# JobAgent

Autonomous job-application agent. Pipeline: **source -> score -> tailor resume -> dispatch -> track**,
with a Streamlit dashboard. Python 3.12, uv, SQLAlchemy, APScheduler, Claude API (structured
output via pydantic), Gmail API, Playwright, Jinja2 + WeasyPrint, Streamlit.
Built in 10 phases; currently Phase 1 (foundation: config, logging, tooling, no business logic).

## Architecture rules
- **Idempotent workers**: re-running any stage must not duplicate work or double-send.
- **State lives in the DB**, never in memory or files between runs.
- **Adapters for external systems** (Gmail, portals, LinkedIn, Claude): business logic depends on
  an interface, not a vendor SDK, so it can be faked in tests.
- **All LLM outputs are pydantic-validated**; invalid output is an error, never passed through.
- Config is split: secrets in `.env` (`Settings`), behavior in `config.yaml` (`AppConfig`).
- The model name comes from `config.yaml` (`llm.model`) only; never hardcode it.

## Hard rules
- `dry_run` defaults to **true**. Nothing is sent externally unless it is explicitly false.
- **Never fabricate resume facts.** Tailoring may reorder/emphasize real content only.
- Respect daily **caps**, **quiet hours**, and the **kill switch** on every dispatch.
- **Pause the channel** on captcha or auth errors; do not retry or work around them.
- **Secrets only via `.env`**. Never read, print, log, or commit real credentials.

## Commands
- Run: `uv run python -m jobagent`
- Test: `uv run pytest`
- Lint: `uv run ruff check .` and `uv run ruff format .`
- Deps: `uv add <pkg>` / `uv add --dev <pkg>` (commit `uv.lock`)

## Working style
- Minimal and boring: no speculative abstractions, no unused dependencies.
- Type hints on all functions; small, readable modules.
- Add a test with every behavior change; keep `pytest` and `ruff check` green.
- Record non-obvious choices in `docs/decisions.md`.
- Propose a plan before large changes; challenge decisions that look wrong.
