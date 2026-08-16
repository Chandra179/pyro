# cron/

Scheduled jobs that replace manual dashboard buttons with a periodic scan.

## synthesize_pending.sh

Runs `pyro synthesize-pending`, which synthesizes docs for every company that
has extracted articles not yet routed into a doc (freeform mode only —
structured mode always rebuilds everything, so there's no "pending" state to
detect there). Safe to run on a tight schedule: a company with nothing new is
a fast no-op, not a full rebuild (see `run_freeform_synthesis`).

Add to crontab (`crontab -e`), e.g. every 15 minutes:

```
*/15 * * * * /path/to/pyro/cron/synthesize_pending.sh >> /var/log/pyro-synth.log 2>&1
```

Requires the same environment the dashboard/CLI need to run standalone:
`OPENROUTER_API_KEY` (or whichever provider key `config/config.yaml` is
routed to) available to the cron user's shell, and ArangoDB reachable
(`make db-up`). If your crontab doesn't source `.env`, either export the
needed vars in the crontab itself or point `synthesize_pending.sh` at an
env file before the `uv run` call.

Once this is wired up, the dashboard's manual "Run synthesis" button
(`api/jobs.py`'s `submit_synthesis`/`SYNTH_RUNS`) becomes optional — kept as a
manual override for triggering an out-of-cycle run, not the primary trigger.
