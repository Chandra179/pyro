You are canonicalizing relationship predicates for a system-map extraction pipeline. Each phrase
below is how a blog post described one system relating to another (e.g. "syncs data into",
"fronts requests for"), but a deterministic synonym match couldn't place it in this pipeline's
fixed predicate vocabulary.

For each phrase, pick the single predicate from the vocabulary below that most precisely captures
what the phrase means — or `null` if none of them genuinely fit. A wrong match corrupts the graph
with a relationship the article didn't actually describe; a missed match just leaves one edge less
specific than it could be. When unsure, answer `null` rather than guessing at the closest-sounding
option.
