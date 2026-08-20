.PHONY: install test lint lint-js run sample-run db-up db-down dashboard dashboard-css dashboard-css-watch merge-graph-pending

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

# Lints and format-checks dashboard/static/js/app.js and dashboard/static/src/graph/*.jsx (the
# hand-written JS/JSX — vendored htmx and the built React Flow bundle are excluded). Separate
# from `lint` rather than a dependency of it: `make install`
# doesn't run dashboard's `npm install` (see dashboard/README.md), so folding this into the
# default `lint` target would break for anyone who hasn't set up the dashboard JS toolchain yet.
lint-js:
	cd dashboard && npm run lint:js && npm run format:js:check

# htmx + Jinja2 + Tailwind dashboard for submitting sitemap URLs and watching
# scrape -> clean -> extract -> merge-graph run. Requires db-up + OPENROUTER_API_KEY.
# CSS/JS are pre-built and committed under dashboard/static/ — you only need
# `dashboard-css` after editing a template's classes or dashboard/static/src/input.css.
#
# .env is sourced into this recipe's own shell (not left to uvicorn or python-dotenv) so
# UVICORN_PORT (see .env.example) is already in the environment before uvicorn's CLI parses its
# options — uvicorn's click command reads UVICORN_<OPTION> via auto_envvar_prefix at that point,
# which is *before* api/main.py's own load_dotenv() call ever runs, so setting the var any later
# would be too late to affect which port it binds.
#
# Kills whatever's already bound to that port before launching, so re-running `make dashboard`
# after a crash/Ctrl-C-that-didn't-take doesn't fail on "address already in use" — `|| true`
# because fuser exits non-zero when the port was already free, which isn't an error here.
dashboard:
	set -a; [ -f .env ] && . ./.env; set +a; \
	fuser -k -n tcp "$${UVICORN_PORT:-8000}" >/dev/null 2>&1 || true; \
	uv run uvicorn api.main:app --reload

# Manually trigger what cron/merge_pending.sh runs on a schedule — merges every company with
# extracted articles not yet folded into its entity graph. Requires db-up. Not run automatically
# by `dashboard` on purpose: the schedule is meant to be independent of whether the dashboard
# process is up (see cron/README.md) — register cron/merge_pending.sh in crontab for the actual
# recurring job instead of relying on this target.
merge-graph-pending:
	uv run pyro merge-graph-pending
