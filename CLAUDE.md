# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in
this repository.

## Project Overview

AI voice assistant that runs entirely on local, open-source infrastructure — no cloud
services. Monorepo of three independent uv-managed Python packages: `client/` (mic
capture + playback; Windows/macOS/Linux), `server/` (STT → LLM → TTS pipeline +
long-term memory; GPU machine), `setup/` (install wizard).

## The specification is canonical

**`docs/spec/` is the single source of truth** for behaviour, architecture, protocol,
data model, and vocabulary:

- [docs/spec/SPEC.md](docs/spec/SPEC.md) — how the spec ↔ test ↔ code alignment loop
  works (requirement IDs, test citations, drift rules). **Follow it**: any code change
  that alters observable behaviour, a format, a threshold, or an invariant updates the
  affected `FR-`/`TR-`/`INV-` requirement in the same commit; tests cite the IDs they
  verify in their docstrings.
- [docs/spec/GLOSSARY.md](docs/spec/GLOSSARY.md) — the ubiquitous language. Use these
  terms exactly; push back on drift (see the incubator CLAUDE.md's Ubiquitous Language
  rule).
- [docs/spec/FUNCTIONAL.md](docs/spec/FUNCTIONAL.md) /
  [docs/spec/TECHNICAL.md](docs/spec/TECHNICAL.md) — the requirements themselves.

Do not restate spec facts here or in other docs — link to them. Where any document
disagrees with the spec, the spec wins and the disagreement is a bug to fix now.

## Hard invariants (details in TECHNICAL.md §Invariants)

Reminders of the rules most likely to be violated accidentally — flag and reject, never
silently work around:

- **INV-1 Live/offline boundary**: the live conversation path writes only JSONL session
  logs; any DB write/extraction/embedding-for-storage bleeding into it must be rejected.
- **INV-3 Single user** — no auth, no concurrency model, by design.
- **INV-4 No barge-in** — the reply plays to completion; do not add interruption logic.
- **INV-5 Session logs kept forever** — no rotation/cleanup without explicit discussion.
- **INV-6 Opacity**: no generic code path may branch on `persona_state` or
  `AssistantPersona.settings` contents.
- **INV-9 Cascade delete** personas → concepts/procedures/directives is load-bearing;
  do not change to SET NULL without explicit discussion.
- **FR-701 Voice-only config scope**: GA settings are DB-backed `User`/`AssistantPersona`
  attributes; `mamemai.toml` is bootstrap-only; install/download/restart concerns belong
  to the wizard, not conversation.

## Session start

Read `docs/PLAN.md` first — a short status summary plus an index of open work. Each
row points at a `docs/changes/<slug>/` folder (`proposal.md` for the problem,
`tasks.md` for the checklist and findings — see `docs/changes/README.md`); update task
markers (`[ ]`/`[~]`/`[x]`) there as work progresses, not in PLAN.md itself.
`docs/BACKLOG.md` holds items not yet promoted to a change. Historical narrative is
archived in `docs/archive/PLAN_phases_1-13.md` and
`docs/archive/PLAN_2026-07-16_to_2026-08-08.md`; the spec is the present tense.

## Evaluation harness (`eval/`)

Benchmarks and quality gates for the whole app live in `eval/`, with its own docs
([PLAN](eval/docs/PLAN.md), [DESIGN](eval/docs/DESIGN.md), [GLOSSARY](eval/docs/GLOSSARY.md)).
**One-way dependency (EV-INV-1):** `eval/` may import `mamemai_server`; nothing in
`server/`, `client/` or `setup/` may ever import `eval/`. Read `eval/docs/PLAN.md` first
when working there.

## Environment & running

Python 3.13+; each package has its own venv. **uv only** — never pip (see incubator
CLAUDE.md / memory: hard rule).

```bash
# Server (GPU machine)
cd server && uv sync
.venv/bin/mamemai-server            # Linux/macOS (.venv/Scripts/… on Windows)

# Client
cd client && uv sync
.venv/Scripts/mamemai-client        # Windows (current dev OS)

# Setup wizard / bundle install
cd setup && uv sync && uv run mamemai-setup
cd server && uv run mamemai-bundle install <bundle-dir>
```

Dev environment: **this machine is `linux_workstation_amdgpu`, the GPU workstation itself**
(AMD GPU, Postgres 18.4 on 5432, Ollama on 11434). `mamemai`/`mamemai_test` databases and
the `mamemai` role (peer auth, no password — `pg_ident.conf`/`pg_hba.conf` mapping this
OS user to it) exist on this machine as of 2026-08-06, mirroring the older `memai`/
`memai_test` databases' setup exactly (both still present, untouched, not this project's
to drop). The `server/` venv is complete (psycopg, pgvector, openai, kokoro,
faster-whisper all import), so the **full suite runs here**: `uv run pytest` for unit
*and* integration (`MAMEMAI_TEST_DATABASE_URL=postgresql:///mamemai_test?user=mamemai`
if the default password-based DSN in `conftest.py` doesn't match this peer-auth setup).
`uv run --no-sync` is still the safe habit, and `--system-certs` is what fixes the
corporate-proxy TLS failure if a sync is needed.

## Linting

Ruff at the monorepo root, `line-length = 120`, tests excluded. Not in any venv —
`uv tool install ruff`, then `ruff check .` / `ruff format .` from the root.

## Testing

- pytest; test pyramid (many unit / fewer integration / few E2E).
- Fakes over mocks for every port (see incubator CLAUDE.md).
- Unit tests: `cd server && uv run pytest tests/unit` (no GPU/DB). Full suite needs
  real Postgres + models (workstation).
- `server/tests/e2e/` — manual-only LLM quality gates against a real running server
  (real model output, not `FakeLLMService`), never picked up by a bare `pytest`/CI run
  (skip unless their `MAMEMAI_TEST_*` env vars are set). Report cards for a design/model
  pairing, not regression tests — see each module's docstring for setup.
- New/touched tests cite the spec IDs they verify: `"""Spec: INV-12, FR-602 — …"""`.

## LLM model guidance (operational)

Wizard default is `llama3.1:8b` (~8B, as of 2026-08-06) — occasionally wobbles
`response_language` but self-corrects on the next turn without prompting. `aya-expanse`
is still catalogued but demoted: once its `response_language` drifts it never recovers
for the rest of the session, confirmed across repeated live tests. Either can be
overridden at install/re-run time, so check `mamemai.toml [llm] model` for what a given
machine is actually running rather than assuming the wizard default — this session
picked `mistral-nemo` instead for its first live run, still being monitored. Avoid
~70B-class models (VRAM eviction, cold-reload stalls) and reasoning models (`<think>`
blocks get spoken aloud) — rationale in spec TR-952.
