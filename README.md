# pyro

Turns any company's engineering blog into a system map: one diagram per company showing the
services, datastores, queues, and external systems its blog history describes, and how they
relate — stored as a graph in ArangoDB (collections `entities`/`relationships`) instead of files
on disk.

See [`docs/architecture.md`](docs/architecture.md) for the full system design — pipeline stages,
data model, model routing, and the dashboard's job/streaming architecture.

## Setup

```bash
make install                 # uv sync + playwright chromium install
cp .env.example .env         # fill in OPENROUTER_API_KEY and ARANGO_* at minimum
docker compose up -d         # starts ArangoDB (see docker-compose.yml)
```

ArangoDB's web UI is at http://localhost:8529 (default user `root`, password from
`ARANGO_ROOT_PASSWORD`) if you want to browse the `articles`/`entities`/`relationships`
collections directly.

## Usage

The easiest way to run the full pipeline (scrape → clean → extract → merge into the graph) is
`run_pipeline.py` at the repo root — it's the one file to edit when you want to point
at a different company/blog:

```python
COMPANY_NAME = "Netflix"
SITEMAP_URL = "https://netflixtechblog.com/sitemap/sitemap.xml"
LIMIT = 10  # cap on newly-scraped articles per run; None for the full blog

# Config overrides (optional) — only set the keys you want to change; unset
# keys fall through to config/config.yaml.
OVERRIDES = {
    # "extraction_concurrency": 2,
}
```

Edit those constants, then:

```bash
make run
```

`LIMIT` caps the number of newly scraped articles — use a small number for a cheap
sample-validation pass before scaling to a full blog, then set it to `None`.

Or drive each stage independently via the CLI, e.g. to re-run just one stage:

```bash
uv run pyro scrape --company-name Netflix --sitemap-url https://netflixtechblog.com/sitemap/sitemap.xml --limit 10
uv run pyro clean
uv run pyro extract
uv run pyro merge-graph --company-name Netflix
uv run pyro graph --company-name Netflix   # list the entities/relationships stored in ArangoDB
```

`uv run pyro run-all --company-name ... --sitemap-url ... --limit ...` runs all four
stages in one CLI call, same as `run_pipeline.py` but with flags instead of edited constants.

Pipeline state (scraped/cleaned/extracted article data) and the merged entity graph both live in
ArangoDB — one database (`pyro` by default), three collections (`articles`, `entities`,
`relationships`), everything scoped by `company_name` so one instance serves every company you
run the pipeline against.

## Testing

```bash
make test
```

All unit tests run offline (no API keys or network required).

## Configuration

Two layers, kept deliberately separate:

- **`.env`** — secrets only (API keys). Copy `.env.example` and fill in what you have.
- **`config/config.yaml`** — everything else: cascade model lists/retry policy, scraping (UA,
  concurrency, Cloudflare challenge handling), sitemap filtering, cleaning (boilerplate
  tags/selectors), chunking, and the graph-merge model/domain taxonomy.
  Loaded by `pyro.config.Settings` (see `src/pyro/config.py`) — edit the YAML to retune the
  pipeline instead of touching code, or override individual keys per-run via
  `run_pipeline.py`'s `OVERRIDES` dict.

API keys (`.env`):

- `OPENROUTER_API_KEY` — required for the extraction cascade's free tier (curated free models + `openrouter/free` meta-router).
- `GROQ_API_KEY` — optional, adds Groq Cloud's fast free tier as an extra fallback.
- `GOOGLE_API_KEY` / `GEMINI_API_KEY` — optional, adds direct Google AI Studio (bigger context window, no OpenRouter middleman) as a fallback.
- `TOKENROUTER_API_KEY` — optional, adds [TokenRouter](https://api.tokenrouter.com) (`api.tokenrouter.com`) as a paid OpenAI-compatible multi-provider fallback, wired in via litellm's generic `openai/` + `api_base` passthrough (no bespoke client needed). No confirmed free tier — model configured via `router.tokenrouter_model` in `config.yaml`.
- `OPENAI_API_KEY` — optional, paid fallback tier used as a last resort.

Every tier is additive and env-gated — set only `OPENROUTER_API_KEY` to run with just the free OpenRouter cascade, or add any subset of the others to extend the fallback chain. Order in the cascade is fixed: OpenRouter free models → Groq → direct Gemini → TokenRouter → paid OpenAI (see `pyro/router/cascade.py`).

ArangoDB connection (`.env`):

- `ARANGO_ROOT_PASSWORD` — read by the `arangodb` container itself (see `docker-compose.yml`).
- `ARANGO_HOST` / `ARANGO_USERNAME` / `ARANGO_PASSWORD` — how the app connects. Defaults
  (`http://localhost:8529`, `root`) match the docker-compose setup out of the box; the
  non-secret connection shape (database/collection names) lives in `config.yaml`'s `arango:`
  block instead.

Any flat top-level `config.yaml` key can still be overridden per-environment via an
identically-named env var (e.g. `EXTRACTION_CONCURRENCY=10`) — env/`.env` takes priority
over the YAML, which takes priority over the code defaults.
