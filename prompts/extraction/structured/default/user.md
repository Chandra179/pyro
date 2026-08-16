Read the following technical blog post and summarize it into structured JSON.

ARTICLE METADATA:
- Title: {title}
- Source URL: {url}

ARTICLE CONTENT:
{content}

EXTRACTION INSTRUCTIONS:
1. `domain` (string): Classify the article into exactly one of these domains: {domains}. Pick the closest match
   for what the article's system primarily does. Use "Other" if none of the listed domains fit — never invent a
   new domain name.
2. `topic` (string): 1-2 sentences on what the article is about — the system or subject it covers.
3. `problem` (string): 1-2 sentences on the problem being solved, if the article describes one. Empty string if
   the article doesn't frame a specific problem.
4. `solution` (string): 1-2 sentences on how the problem was solved, or what the system does, in the article's
   own terms. Empty string if there's no clear solution described.

Return strict, valid JSON with exactly these four fields. Stick to what the article says — do not add facts,
numbers, or reasoning that aren't in the text.
