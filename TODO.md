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

## 5. Post-hoc graph algorithms for vocabulary/quality auditing

KGGen-style periodic clustering (embed the `relation_phrase` values sitting under a canonical
relation — especially the `depends_on` fallback bucket — and cluster to surface phrasings the
static synonym table is missing) plus structural checks (cycle detection on `deploys`/`owns` edges,
centrality for dashboard use).