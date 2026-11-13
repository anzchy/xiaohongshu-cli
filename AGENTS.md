# AGENTS.md

Fork of [jackwener/xiaohongshu-cli](https://github.com/jackwener/xiaohongshu-cli) (`origin` = anzchy fork, `upstream` = jackwener).

## Dev

- Setup: `uv sync --extra dev` (pytest lives in the `dev` extra).
- Tests: `uv run pytest -q` (unit, mocked HTTP). Real-API tests: `uv run pytest -m smoke` (needs a logged-in session; use sparingly — captcha risk).
- Lint: `uv run ruff check .`

## Agent skills

### Issue tracker

GitHub Issues on `anzchy/xiaohongshu-cli` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/` (created lazily). See `docs/agents/domain.md`.
