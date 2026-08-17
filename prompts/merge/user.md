Company: {company_name}

EXISTING SYSTEM NAMES (already in the graph — reuse one of these exactly if a system below is
the same real thing):
{existing_entity_names}

ARTICLE: {title}

SYSTEMS THIS ARTICLE MENTIONS:
{article_entities_json}

For every system listed above, decide its canonical name: either one of the existing names (if
it's the same system) or the article's own name unchanged (if it's new). Return strict, valid
JSON with exactly one field:

{{
  "resolved": [
    {{"article_name": "...", "canonical_name": "..."}}
  ]
}}

Include exactly one entry per system listed above, in any order.
