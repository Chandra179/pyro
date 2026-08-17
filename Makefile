.PHONY: install test lint run sample-run db-up db-down dashboard dashboard-css dashboard-css-watch merge-graph-pending

install:
	uv sync
	uv run playwright install chromium

# ArangoDB (see docker-compose.yml) — required before running scrape/clean/extract/merge-graph.
db-up:
	docker compose up -d

db-down:
	docker compose down

test:
	uv run pytest

lint:
	uv run ruff check .

# htmx + Jinja2 + Tailwind dashboard for submitting sitemap URLs and watching
# scrape -> clean -> extract -> merge-graph run. Requires db-up + OPENROUTER_API_KEY.
# CSS/JS are pre-built and committed under dashboard/static/ — you only need
# `dashboard-css` after editing a template's classes or dashboard/static/src/input.css.
dashboard:
	uv run uvicorn api.main:app --reload


# Manually trigger what cron/merge_pending.sh runs on a schedule — merges every company with
# extracted articles not yet folded into its entity graph. Requires db-up. Not run automatically
# by `dashboard` on purpose: the schedule is meant to be independent of whether the dashboard
# process is up (see cron/README.md) — register cron/merge_pending.sh in crontab for the actual
# recurring job instead of relying on this target.
merge-graph-pending:
	uv run pyro merge-graph-pending
