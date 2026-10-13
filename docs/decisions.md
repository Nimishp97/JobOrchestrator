# Decisions

## 001. Use uv for environment and dependency management
- **Decision**: uv with `pyproject.toml` and a committed `uv.lock`; Python pinned to 3.12.
- **Why**: one fast tool for Python install, venv, lockfile, and running. Reproducible installs
  matter for an agent that takes real-world actions.
- **Trade-off**: contributors need uv installed; it is a newer tool than pip/poetry.

## 002. Split secrets (.env) from behavior (config.yaml)
- **Decision**: secrets via `.env` (pydantic-settings); behavior via committed `config.yaml`
  validated by pydantic models with `extra="forbid"`.
- **Why**: behavior is reviewable and versioned, secrets are never committed. A typo in a key
  fails startup instead of silently using a default.
- **Trade-off**: two config sources to understand.

## 003. dry_run defaults to true
- **Decision**: `dry_run=True` in the model and in `config.yaml`; sending requires an explicit
  `false`.
- **Why**: the agent sends email and applications on the user's behalf. A wrong default or a
  missing config must fail safe, not send.
- **Trade-off**: one extra step to go live.

## 004. The LLM model name is configurable, with no default in code
- **Decision**: `llm.model` is a required string in `config.yaml`; no model ID appears in code.
- **Why**: models get deprecated; swapping should be a config change, and a missing value should
  fail fast rather than silently use a stale model.
- **Trade-off**: `config.yaml` is mandatory.

## 005. Redact secrets in the log formatter
- **Decision**: a formatter replaces known secret values in the final log text (including
  tracebacks). Secrets are held as `SecretStr`.
- **Why**: `SecretStr` does not protect against code that unwraps the value into a message.
- **Limit**: only redacts values known at startup; do not log secrets deliberately.

## 006. Separate Job and Application state machines
- **Decision**: `Job` (discovered -> scored -> shortlisted/skipped -> applied, plus failed) and
  `Application` (drafted -> queued -> sending -> sent -> replied/interview/rejected/no_response,
  plus failed/withdrawn) are independent. Transition rules are dicts in
  `jobagent/db/state_machine.py`; the only way to change status is `transition_job` /
  `transition_application`, which write the status and the audit event in one transaction.
- **Why**: one job can have several application attempts across channels, and "what we think of
  the job" and "what happened to a send" evolve independently. Rules as data are testable
  exhaustively (every legal and illegal pair is a test case).
- **Additions to the original spec**: `sending` (see 007), `skipped -> shortlisted` (manual
  override), `no_response -> replied` and `replied -> interview|rejected` (late replies happen).
- **Same-state requests are a no-op** returning False, with no event: workers are at-least-once,
  so "already there" means the work is done. Cost: a second payload for the same state is dropped.

## 007. Unique constraints are the idempotency guard; `sending` makes sends at-most-once
- **Decision**: `UNIQUE(dedupe_key)`, `UNIQUE(source, source_job_id)` on jobs and
  `UNIQUE(job_id, channel)` on applications. Inserts use `ON CONFLICT DO NOTHING`.
  Applications move `queued -> sending` (committed) before any external call.
- **Why**: a unique constraint stops a second row, not a second send. If a worker dies after the
  provider accepted a message but before `sent` is committed, a retry would double-send. A row
  stuck in `sending` is never auto-retried; it needs reconciliation (check the provider by
  `external_message_id`, or a human decides). The cap counter includes `sending` rows for the
  same reason.
- **dedupe_key**: `sha256("v1|" + norm(company) + "|" + norm(title) + "|" + norm(location))`.
  URLs differ across boards for the same job, so they are not the key. Known weakness:
  location spelling variants produce a duplicate row (a false negative, not a lost job);
  fuzzy matching belongs to the scoring phase.

## 008. Lease-based claiming
- **Decision**: `claim_jobs` is one `UPDATE ... WHERE id IN (SELECT ...) RETURNING id` that sets
  `claimed_by` / `claimed_until` on rows in the requested status with no live lease. Expired
  leases match the same predicate, so a crashed worker's jobs are reclaimed automatically.
  Status transitions clear the lease.
- **Why**: SQLite has no `SELECT ... FOR UPDATE`; a single statement is atomic. Tested with
  sequential and threaded claimers.
- **Known limitation**: no fencing token. A worker that overruns its lease can still finish after
  another worker reclaimed the job; the guarded UPDATE in `transition` limits the damage.
  Choose `lease_seconds` above worst-case processing time.

## 009. SQLite in WAL mode, BEGIN IMMEDIATE, foreign keys on
- **Decision**: every connection sets `busy_timeout=5000`, `journal_mode=WAL`,
  `foreign_keys=ON`. Every transaction starts with `BEGIN IMMEDIATE`. `synchronous` is left at
  its default (FULL).
- **Why**: a scheduler writes while a dashboard reads. WAL lets readers proceed during writes.
  Without BEGIN IMMEDIATE, a transaction that reads and then writes can fail instantly with
  SQLITE_BUSY_SNAPSHOT (busy_timeout does not help); BEGIN IMMEDIATE queues writers instead.
  FULL durability because losing a `sent` record means a double send.
- **Consequence**: a read-only dashboard session should use a separate read engine, otherwise it
  takes the write lock.
- **Migrations** use `render_as_batch=True` and run with foreign keys off (batch mode recreates
  tables). Constraints follow a naming convention so batch migrations can alter them.
  `create_all()` is not used anywhere; even the tests build their database via migrations.

## 010. The event log is append-only, enforced by the database
- **Decision**: no update or delete code exists for `Event`, and two SQLite triggers abort any
  UPDATE or DELETE on `events`.
- **Why**: an audit trail that code can rewrite is not an audit trail. Triggers also stop bulk
  SQL and future bugs. They live in the migration (the one place DDL is raw SQL).
- **Timestamps** are stored as timezone-aware UTC via a `UTCDateTime` type that rejects naive
  datetimes (SQLite itself discards tzinfo).
