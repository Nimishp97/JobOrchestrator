# JobAgent

Autonomous job-application agent: **source -> score -> tailor resume -> dispatch -> track**,
with a Streamlit dashboard (later phases). This repo is at **Phase 2**:
config, logging, tooling, and the database layer with state machines (no pipeline logic yet).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it will fetch Python 3.12 automatically).

```bash
git clone <repo-url> jobagent && cd jobagent
uv sync                          # install pinned deps from uv.lock
cp .env.example .env             # then edit .env with your real values (never commit it)
uv run pre-commit install        # enable the ruff pre-commit hook
uv run python -m jobagent.db init   # create data/jobagent.db by applying migrations
```

On Windows PowerShell use `Copy-Item .env.example .env`.

## Run

```bash
uv run python -m jobagent
```

Expected: `Config loaded | dry_run=True kill_switch=False ...`. Behavior settings live in
`config.yaml`; secrets live only in `.env`.

Inspect the database: `uv run python -m jobagent.db status` (row counts per table and status).

## Test and lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Layout

- `jobagent/` package (`sources`, `scoring`, `resume`, `dispatch`, `tracker`, `db`, `llm`)
- `config.yaml` behavior config (committed, safe defaults)
- `.env` secrets (gitignored; see `.env.example`)
- `docs/decisions.md` architecture decision log, `CLAUDE.md` rules for AI collaborators

