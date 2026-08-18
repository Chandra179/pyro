# TODO

Improvement items identified from reviewing pyro's extract/merge/storage layers against the
knowledge-graph-construction literature (papers, production KG systems, entity-resolution
practice). Not exhaustive — scoped to what surfaced from that review. See conversation history /
git log for the fuller rationale behind each; this file tracks status, not the discussion.

## 2. Relation canonicalization has no LLM fallback tier

Entity name resolution is two-tier (deterministic string match, then LLM for what's left) —
`graph/resolve.py` / `graph/merge.py`. Relation canonicalization (`extract/schema.py`'s
`normalize_relation`) is deterministic-only; an unrecognized phrase falls back to `depends_on` with
just a log line (shipped). EDC ([Extract, Define, Canonicalize](https://arxiv.org/pdf/2404.03868))
is the proven pattern for the next tier: have the LLM define what an unmatched relation phrase
means, then canonicalize via embedding similarity against the existing vocabulary instead of a
static synonym table.

**Do not build speculatively.** Only worth doing once the fallback-warning log (already shipped)
shows a non-trivial real fallback rate. At last check (14 edges, 1 company) the fallback rate was
0%.

2. Onboard company #2. That's the actual trigger for a chunk of this work to become real — you'd get to validate the concurrency caps against genuine concurrent load (not the synthetic timing tests I wrote), and start generating the volume/drift data #2 and #5 are waiting on.


## 5. Post-hoc graph algorithms for vocabulary/quality auditing

KGGen-style periodic clustering (embed the `relation_phrase` values sitting under a canonical
relation — especially the `depends_on` fallback bucket — and cluster to surface phrasings the
static synonym table is missing) plus structural checks (cycle detection on `deploys`/`owns` edges,
centrality for dashboard use). **Correctly deferred**: pyro's graph is currently far too small (20
entities, 14 relationships, one company) for clustering or centrality to produce a meaningful
signal — this is worth building once real data volume exists, not before.

---
(Item 4 — a domain-taxonomy fallback-rate check, the `DOMAINS` analogue of item 2 — was proposed in
conversation but not included here at the user's direction; add it back if it becomes a priority.)

## Scale/growth watchlist (not KG-literature-driven — general architecture review)

Reviewed the pipeline/dashboard/cron layers for what breaks first as data and company count grow,
specifically with multiple companies being scraped in mind. Unlike items 1/3/5, these don't need a
data signal to know what to build — concurrency caps are a solved pattern, not a design unknown —
so debated with the user whether to build ahead of need. Verdict: fix what's cheap/non-speculative
now (a dead config field, and concurrency caps that are cheap to add and don't calcify anything if
delayed); leave what needs real load to size correctly for when it's actually exercised.

- **Fixed: dead `extraction_rpm_limit` config field (done).** Existed in `config.py`, documented as
  bounding extraction concurrency, but was never referenced anywhere else — the real cap is
  `extraction_concurrency`'s `asyncio.Semaphore` in `extract/pipeline.py`'s `run_extraction`.
  Removed the unused field; folded its intent into a comment on the field that actually does the
  work, rather than leaving a misleading no-op setting in the config surface.
- **Fixed: `api/jobs.py`'s `submit_job` had no cap on concurrently *active* jobs (done).** Added
  `Settings.max_concurrent_jobs` (default 3) and `_JOB_SLOTS`, a `threading.Semaphore` gating the
  top of `_run_job`. A job submitted beyond the cap sits with `status == "pending"` (an existing
  dashboard state, no new UI needed) until a slot frees. Still spawns a `threading.Thread` per job
  unconditionally (deliberately, to preserve `daemon=True` shutdown semantics) — the semaphore
  bounds *active* Playwright/LLM work, not thread count, which is what actually protects
  CPU/provider-rate-limit capacity.
- **Fixed: `cli.py`'s `_merge_graph_pending_impl` looped companies sequentially (done).** Added
  `Settings.merge_pending_concurrency` (default 3) and parallelized the loop with a
  `ThreadPoolExecutor`. Each company's own merge is still correctly serialized by the existing
  per-company `_MERGE_LOCKS` — only *different* companies now run concurrently, keeping one cron
  tick's wall-clock time from scaling linearly with company count.
- **`api/jobs.py`'s `MAX_RETAINED_JOBS = 50` job-history cap is global, not per-company** — a
  company running frequent jobs can evict another company's recent job history from the dashboard.
  Left as-is: minor UX papercut, not a resource/correctness risk, no urgency.
- **What's already correctly built for this, confirmed, not debt**: entity/relationship storage
  keys (`db/keys.py`) are company-scoped end to end, `KnownNames`/merge resolution is rebuilt fresh
  per company per run with no cross-company state, and the `DOMAINS` taxonomy being fixed/shared
  across companies is a deliberate, documented choice for a future cross-company comparison
  feature (CLAUDE.md) — not something to "fix."
