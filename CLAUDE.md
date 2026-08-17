# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

pyro turns a company's engineering blog into a system map: it crawls a blog's posts, extracts the
services/datastores/queues/external systems each post describes via LLM, and merges what it finds
into one growing, company-wide entity graph stored in ArangoDB. See
[`docs/architecture.md`](docs/architecture.md) for the full design (pipeline stages, model
routing, merge/resolution logic, dashboard streaming architecture) — read it before making
non-trivial changes to any of those layers, it's written to orient before you open code.

## Commands

```bash
make install                 # uv sync + playwright chromium install
make db-up / make db-down    # ArangoDB via docker-compose (required for scrape/clean/extract/merge)
make test                    # uv run pytest
make lint                    # uv run ruff check .
make dashboard                # uv run uvicorn api.main:app --reload (requires db-up + OPENROUTER_API_KEY)
make merge-graph-pending     # manually trigger what cron/merge_pending.sh runs on a schedule
```

Single test: `uv run pytest tests/test_graph_merge.py::test_name`

Pipeline stages via CLI (each `_impl` function is the real internal API — see "CLI structure" below):

```bash
uv run pyro scrape --company-name X --sitemap-url ... [--limit N]
uv run pyro clean [--company-name X]
uv run pyro extract [--company-name X]
uv run pyro merge-graph --company-name X
uv run pyro run-all --company-name X --sitemap-url ...   # all four stages
uv run pyro graph --company-name X                        # list stored entities/relationships
```

Or edit the constants at the top of `run_pipeline.py` (company/sitemap/limit) and run
`uv run python run_pipeline.py` — the intended way to point the whole pipeline at a new blog.
(README and the Makefile's `.PHONY` list reference `make run` for this, but no `run:` recipe
currently exists in `Makefile` — use the `uv run` form, or add the recipe back if you need it.)

`canonicalize-relations` is a one-off command that rewrites already-stored edges onto the
controlled relation vocabulary; a no-op once run, and unnecessary for a fresh database. (README's
"One-off maintenance commands" section also documents `migrate-relationships` — that command and
its underlying `migrate_relationships_to_edges` helper were removed from `src/pyro/db/` since the
README was last updated; the README is stale on that point.)

Dashboard CSS (Tailwind, compiled output is committed): after editing template classes or
`dashboard/static/src/input.css`, run `cd dashboard && npm run build:css` (or `watch:css`).

## Configuration philosophy — three layers, kept separate

- **`.env`** — secrets only (provider API keys, ArangoDB credentials). Never touch what isn't a
  secret here.
- **`config/config.yaml`** — everything else (cascade model lists/retry policy, scrape/clean/chunk
  tuning, domain taxonomy, graph-merge model). Loaded by `pyro.config.Settings`
  (`src/pyro/config.py`). Any flat top-level key is also overridable via an identically-named env
  var. Only add a key here if its value actually differs from `config.py`'s field default —
  restating a default silently shadows future changes to that default.
- **`prompts/`** — the actual model instructions for extraction and merge, editable independent of
  code/config.

Model provider cascade order is fixed and additive (OpenRouter free → Groq → direct Gemini →
TokenRouter → paid OpenAI), gated purely on which keys are set — see `src/pyro/router/cascade.py`.

## Architecture

### CLI structure (`src/pyro/cli.py`)

Every pipeline stage has a plain `_stage_impl(...)` function taking an optional `Settings`
override, plus a thin `@app.command()` typer wrapper with no `settings` param (typer can't parse
a `Settings`-typed CLI arg). **Programmatic callers must call the `_impl` functions directly** —
`run_pipeline.py`, `run-all`, and `api/jobs.py`'s background job runner all do this. The
underscore means "not a typer command," not "private" — these are the real internal API.

Graph merges for a given `company_name` are serialized via a per-company `threading.Lock` in
`cli.py` (`_MERGE_LOCKS`), shared by every entry point (full pipeline jobs, the dashboard's
manual trigger, cron). This is the single choke point that makes it impossible for two merges to
race on the same company's graph — don't add a second path into `run_graph_merge` that bypasses
it.

### Storage (`src/pyro/db/`)

One ArangoDB database, three collections, all scoped by `company_name`:

- `articles` (document) — pipeline state per scraped article (raw HTML → cleaned → extracted),
  see `db/articles.py`.
- `entities` (document) — resolved systems/services, see `db/entities.py`.
- `relationships` (**edge** collection) — `_from`/`_to` handles into `entities`, so the graph is
  AQL-traversable rather than a flat list to reassemble in memory, see `db/relationships.py`.

`Database` (`db/database.py`) is the facade over all three — import it from `pyro.db`, not from
the submodules directly, so internal layout can move freely. `open_db_from_settings` is the
standard way to get a connected instance scoped to a `Settings`.

### Pipeline flow

fetch (`scrape/`, Playwright-rendered — many blogs are client-side rendered) → clean/chunk
(`clean/`) → extract (`extract/`, LLM pulls systems + stated relationships + a fixed domain tag
per post, independently per post) → merge (`graph/merge.py`, folds each post's extraction into
the company's running graph).

Merge does name resolution in two tiers: a free deterministic pass (exact/near-exact string
match) first, then an LLM pass only for names it couldn't settle, shown only the most-similar
known names (keeps prompt size bounded as the graph grows). Merge is **strictly sequential per
company** (not parallel across posts) because each post's resolution needs to see what prior
posts in the same run already settled. `graph/resolve.py` and `graph/backfill.py` are part of
this layer; `graph/prompts.py` / `prompts/merge/` hold the actual prompt text.

### Dashboard (`api/` + `dashboard/`)

Two halves that only communicate over HTTP:

- **`api/`** (repo root) — FastAPI app; the only code that touches the pipeline or database.
  `api/jobs.py` runs the full scrape→clean→extract→merge-graph pipeline per submitted job on a
  background thread with an in-memory job store (run state is **not** persisted — restarting the
  dashboard loses in-flight job status, and it can't scale beyond one instance; this is a known,
  accepted limitation, not a bug to silently fix). `api/sse.py` streams merge-call output live via
  SSE (a push, not a poll — replaced an earlier design that re-sent the whole transcript every
  second). `api/graph_view.py` + `api/render.py` build the Mermaid diagram from stored entities.
- **`dashboard/`** — Jinja2 templates + static assets only, zero Python. See
  [`dashboard/README.md`](dashboard/README.md) for the template inheritance tree and two
  rendering patterns worth knowing before touching either page: the Runs page's job card is never
  wholesale-re-swapped (re-swapping while SSE merge history streams underneath double-appends
  chunks — htmx's SSE extension has no re-registration guard), while the Data page's shell and
  panel poll/swap independently.

### Scheduling (`cron/`)

`cron/merge_pending.sh` runs `pyro merge-graph-pending` on a schedule, independent of whether the
dashboard is running — the primary way a company's graph stays caught up over time without manual
triggering. Safe to run tightly (e.g. every 15 min): a company with nothing pending is a fast
no-op. Not wired into `make dashboard` on purpose — see `cron/README.md`.

## Testing

`make test` runs everything offline — no API keys or network required. `tests/conftest.py`
force-clears all provider API-key env vars before every test (autouse fixture) specifically
because `litellm`'s import-time `load_dotenv()` leaks the real `.env` into the process
environment; don't remove that fixture or provider/router tests will silently pick up real keys.
