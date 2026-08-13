.PHONY: install test lint run sample-run

install:
	uv sync
	uv run playwright install chromium

test:
	uv run pytest

lint:
	uv run ruff check .

# Runs the full pipeline (scrape -> clean -> extract -> synthesize) for the
# company/blog configured at the top of run_pipeline.py — edit that file to
# point at a different blog, or to override config/config.yaml tuning knobs
# via its OVERRIDES dict. Requires OPENROUTER_API_KEY.
run:
	uv run python run_pipeline.py

# Alias matching docs/plan.md "Success Criteria Before Scaling Up".
sample-run: run
