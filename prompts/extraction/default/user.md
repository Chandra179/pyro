Read the following technical blog post and extract its system map as JSON.

ARTICLE METADATA:
- Title: {title}
- Source URL: {url}

ARTICLE CONTENT:
{content}

Available domains for the `domain` tag: {domains}.

Available values for `relation` — use one of these exactly, picking the closest fit:
{relations}

Return strict, valid JSON with exactly two fields:

{{
  "entities": [
    {{"name": "...", "kind": "service|datastore|queue|external_system|library|model|team", "domain": "...", "description": "... or null"}}
  ],
  "relationships": [
    {{"source": "...", "target": "...", "relation": "...", "as_of": "... or null"}}
  ]
}}

`source` and `target` must each be the exact `name` of an entity you listed above. Read
`relation` as "source <relation> target" — so a service that saves records into a database is
`{{"source": "That Service", "target": "That Database", "relation": "writes_to"}}`.

Stick to what the article says — do not add systems, relationships, or dates that aren't in the
text. If the article doesn't clearly describe any relationships between the systems it mentions,
return an empty `relationships` list rather than guessing at one.
