# cron/

Scheduled jobs that replace manual dashboard buttons with a periodic scan.

## merge_pending.sh

Runs `pyro merge-graph-pending`, which folds extracted articles into the entity graph for every
company that has at least one article not yet merged. Safe to run on a tight schedule: a company
with nothing new is a fast no-op, not a full rebuild (see `run_graph_merge`).

Add to crontab (`crontab -e`), e.g. every 15 minutes:

```
*/15 * * * * /path/to/pyro/cron/merge_pending.sh >> /var/log/pyro-merge.log 2>&1
```

Requires the same environment the dashboard/CLI need to run standalone:
`OPENROUTER_API_KEY` (or whichever provider key `config/config.yaml` is
routed to) available to the cron user's shell, and ArangoDB reachable
(`make db-up`). If your crontab doesn't source `.env`, either export the
needed vars in the crontab itself or point `merge_pending.sh` at an
env file before the `uv run` call.

The dashboard itself has no standalone "run merge" trigger — `POST /jobs` (`api/jobs.py`'s
`submit_job`) always runs the full scrape→clean→extract→merge-graph pipeline, not just the merge
stage. This cron job is the primary way pending merges get picked up outside of a full pipeline
run (e.g. after editing the merge prompt and re-running merge-graph-pending without
re-scraping), not a redundant backstop to a UI feature.
