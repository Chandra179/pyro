#!/usr/bin/env bash
# Cron entry point for auto-merging companies that have extracted articles not yet folded into
# their entity graph. Replaces the dashboard's manual "Run merge" action — see README.md in this
# directory for crontab setup.
#
# Runs from the repo root so config/config.yaml and .env resolve the same way
# they do for `uv run pyro ...` in a dev shell.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec uv run pyro merge-graph-pending
