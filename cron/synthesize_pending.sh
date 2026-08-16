#!/usr/bin/env bash
# Cron entry point for auto-synthesizing companies that have unrouted extracted
# articles (freeform mode). Replaces the dashboard's manual "Run synthesis"
# button — see README.md in this directory for crontab setup.
#
# Runs from the repo root so config/config.yaml and .env resolve the same way
# they do for `uv run pyro ...` in a dev shell.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec uv run pyro synthesize-pending
