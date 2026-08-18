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
- `kind`: one of `service`, `datastore`, `queue`, `external_system`, `team` — pick the closest fit.
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
