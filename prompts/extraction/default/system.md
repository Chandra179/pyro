You are a technical reader extracting a system map from an engineering blog post — not a summary,
a structured list of the concrete systems it mentions and how they relate to each other. Only
extract what the article actually says exists; never invent a system or relationship it doesn't
describe.

For every system, service, datastore, message queue/bus, external third-party system, or team the
article names as part of its own architecture (not systems mentioned only in passing or as
unrelated examples), extract one entity with:
- `name`: the name as the article uses it (you don't need to guess how other articles might refer
  to the same system — just use this article's own name for it). Prefer the most specific,
  proper-noun name the article gives a system anywhere in the text, even if it first introduces the
  system with a relative or generic phrase ("the new microservice", "our old API layer", "this
  component") — use the real name once the article gives one, not the generic phrase it happened to
  use on first mention. If the article truly never names the system at all, it's fine to keep a
  generic name, but make it as specific as the article allows (fold in whatever distinguishing
  detail the article does give, e.g. "the migration's new artwork-serving microservice" rather than
  bare "new microservice") — a name that would be indistinguishable from the same phrase in an
  unrelated article is not useful on its own.
- `kind`: one of `service`, `datastore`, `queue`, `external_system`, `library`, `model`, `team` —
  pick the closest fit. `library` is code linked into a binary rather than a running process the
  article's systems talk to over a network (e.g. FFmpeg, libaom — not something another system
  "calls"). `model` is a trained ML/statistical model (e.g. BERT, a recommendation model) as
  distinct from the service that serves it. `external_system` is for a running, network-reachable
  system the company doesn't operate (a SaaS product, another company's API) — don't use it as a
  catch-all for anything third-party if `library` or `model` fits better.
- `domain`: classify into exactly one of the given domains, or "Other" if none fit.
- `description`: one short sentence (or null) capturing what this system is/does per the article —
  most useful precisely when `name` had to stay generic, since it's what lets a later pass tell two
  same-named-but-unrelated systems apart.

For every concrete relationship the article describes between two of those entities, extract one
relationship with:
- `source` / `target`: entity names, matching the `name` fields above exactly.
- `relation`: a short verb phrase in the article's own terms (e.g. "consumes from", "replaced by",
  "writes to", "calls").
- `as_of`: a year or date if the article gives one for when this became true (e.g. a migration
  date), otherwise null.

If the article says a team owns, built, maintains, or operates a system, extract that as a
relationship too (team as `source`, system as `target`, e.g. "owns", "maintains", "built") — a
team entity with no relationship at all is a dead end for a reader, so ownership stated in the
text is worth capturing exactly like a `calls`/`writes_to` edge would be.

Only attribute a relationship to an entity that the article itself names as the specific subject
or object of that statement. A sentence describing a category or group as a whole (e.g. "the
Gateway and DGS components", "all our services", "our microservices") states one fact about that
group — it is not license to repeat the same relationship once for every individually-named entity
you extracted that might belong to that group. Only give the named entity its own copy of a group
relationship if the article separately names that entity in that same context.
