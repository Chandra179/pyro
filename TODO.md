# TODO

Improvement items identified from reviewing pyro's extract/merge/storage layers against the
knowledge-graph-construction literature (papers, production KG systems, entity-resolution
practice). Not exhaustive — scoped to what surfaced from that review. See conversation history /
git log for the fuller rationale behind each; this file tracks status, not the discussion.

## 5. Post-hoc graph algorithms for vocabulary/quality auditing

KGGen-style periodic clustering (embed the `relation_phrase` values sitting under a canonical
relation — especially the `depends_on` fallback bucket — and cluster to surface phrasings the
static synonym table is missing) plus structural checks (cycle detection on `deploys`/`owns` edges,
centrality for dashboard use).