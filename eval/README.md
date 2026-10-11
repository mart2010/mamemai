# mamemai-eval

Evaluation harness for the whole Mamemai app. Start with [docs/PLAN.md](docs/PLAN.md),
then [docs/DESIGN.md](docs/DESIGN.md) and [docs/GLOSSARY.md](docs/GLOSSARY.md).

```bash
cd eval && uv sync --system-certs          # light core; add --extra sut for the System Under Test
uv run pytest tests/unit                    # T0: no DB, no network
uv run mamemai-eval db migrate              # creates schema `eval` in mamemai_eval
uv run pytest tests/integration             # uses mamemai_eval_test (auto-created), never mamemai_eval
```
Database: `MAMEMAI_EVAL_DATABASE_URL` (default `postgresql:///mamemai_eval`).

Corpus commands: `mamemai-eval corpus freeze|verify|list|import`, `mamemai-eval generate ledger|probes [--language L --archetype A --seed N]`.
