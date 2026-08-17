Read the following technical blog post and extract its system map as JSON.

ARTICLE METADATA:
- Title: {title}
- Source URL: {url}

ARTICLE CONTENT:
{content}

Available domains for the `domain` tag: {domains}.

Return strict, valid JSON with exactly two fields:

{{
  "entities": [
    {{"name": "...", "kind": "service|datastore|queue|external_system|team", "domain": "..."}}
  ],
  "relationships": [
    {{"source": "...", "target": "...", "relation": "...", "as_of": "... or null"}}
  ]
}}

Stick to what the article says — do not add systems, relationships, or dates that aren't in the
text. If the article doesn't clearly describe any relationships between the systems it mentions,
return an empty `relationships` list rather than guessing at one.
