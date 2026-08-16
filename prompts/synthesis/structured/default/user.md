I have collected per-article summaries from {total_articles} engineering blog posts for the engineering team at
{company_name}, all classified under the "{domain}" domain. Each entry has a `title`, and extracted `topic`
(what the article covers), `problem` (what it solves, if stated), and `solution` (how it solves it).

YOUR TASK:
Write a single narrative architecture document scoped to the "{domain}" domain only, grouping related articles
into subsystems or themes within that domain and explaining each one in prose — not a per-article catalog.
Prioritize depth on the themes with the clearest problem/solution pairs across multiple articles; group thin,
one-off topics into a shorter summary section instead of forcing them into full sections.

HOW TO PROCESS THE DATA:
1. GROUP BY THEME: cluster articles that describe the same system or a shared problem space within this domain,
   and merge them into one section rather than one section per article.
2. PICK 2-5 DEEP-DIVE SECTIONS: choose the themes with the richest problem/solution material and explain each —
   what problem it addresses and how it's solved — in real prose.
3. ONE DIAGRAM MAX PER SECTION: a Mermaid `graph TD`/`graph TB` for this domain's topology near the top is fine;
   inside a deep-dive, add at most one small diagram only if the problem/solution text clearly describes a flow
   or relationship between components — don't force one otherwise.
4. NO CODE SNIPPETS: never include fenced code blocks.
5. CROSS-CUTTING PATTERNS LAST: close with a short synthesis of patterns actually evidenced by repeated
   problems/solutions across the posts in this domain — not generic industry boilerplate, and not a repeat of
   points already made above.

LOOSE DOCUMENT SHAPE (adapt to what the data supports — do not pad missing sections):

# {company_name}: {domain}

[1-2 paragraph framing of the core problems this domain's articles reveal the team is solving.]

## Big Picture: {domain} Topology
```mermaid
graph TD
  %% major systems within this domain only
```

## [Deep-dive theme name]
[Prose: the problem, how it's solved, grouping the articles that belong to this theme.]

[... repeat ...]

## Other Topics in This Domain
[Compact table or short paragraph for thin, one-off topics.]

## Cross-Cutting Patterns
[Only patterns actually evidenced across multiple posts in this domain.]

ARTICLE SUMMARIES:
{facts_json_data}
