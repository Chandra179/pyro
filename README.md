# pyro

Turns any company's engineering blog into a single `architecture.md` blueprint,
complete with a Mermaid.js topology diagram — see `docs/plan.md` for the full design.

## Setup

```bash
make install          # uv sync + playwright chromium install
cp .env.example .env  # fill in OPENROUTER_API_KEY at minimum
```

## Usage

Run stages independently:

```bash
uv run pyro scrape --company-name Netflix --sitemap-url https://netflixtechblog.com/sitemap/sitemap.xml --db data/netflix.db --limit 10
uv run pyro clean --db data/netflix.db
uv run pyro extract --db data/netflix.db
uv run pyro synthesize --db data/netflix.db --company-name Netflix --out architecture.md
```

Or end-to-end:

```bash
uv run pyro run-all --company-name Netflix --sitemap-url https://netflixtechblog.com/sitemap/sitemap.xml --db data/netflix.db --limit 10
```

`--limit` caps the number of newly scraped articles — use it for the small-sample
validation pass recommended in `docs/plan.md` before scaling to a full blog.

## Testing

```bash
make test
```

All unit tests run offline (no API keys or network required).

## Configuration

Environment variables (see `pyro/config.py`):

- `OPENROUTER_API_KEY` — required for the extraction cascade's free tier (curated free models + `openrouter/free` meta-router).
- `GROQ_API_KEY` — optional, adds Groq Cloud's fast free tier (llama-3.3-70b-versatile) as an extra fallback.
- `GOOGLE_API_KEY` / `GEMINI_API_KEY` — optional, adds direct Google AI Studio (bigger context window, no OpenRouter middleman) as a fallback.
- `TOKENROUTER_API_KEY` — optional, adds [TokenRouter](https://api.tokenrouter.com) (`api.tokenrouter.com`) as a paid OpenAI-compatible multi-provider fallback, wired in via litellm's generic `openai/` + `api_base` passthrough (no bespoke client needed). No confirmed free tier — model defaults to `TOKENROUTER_MODEL` (`deepseek/deepseek-v4-flash-0731`).
- `OPENAI_API_KEY` — optional, paid fallback tier used as a last resort.

Every tier is additive and env-gated — set only `OPENROUTER_API_KEY` to run with just the free OpenRouter cascade, or add any subset of the others to extend the fallback chain. Order in the cascade is fixed: OpenRouter free models → Groq → direct Gemini → TokenRouter → paid OpenAI (see `pyro/router.py`).
- `EXTRACTION_CONCURRENCY`, `EXTRACTION_RPM_LIMIT`, `CHUNK_TOKEN_THRESHOLD`,
  `SYNTHESIS_BATCH_SIZE`, `SYNTHESIS_MODEL` — pipeline tuning knobs.
