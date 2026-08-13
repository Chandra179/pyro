"""Synthesis prompts, verbatim from plan.md 'Prompt 2: Global Architecture Synthesis'."""

SYNTHESIS_SYSTEM_PROMPT = """
You are a Principal Enterprise Architect tasked with reverse-engineering a tech company's global software architecture from a collection of extracted engineering blog facts.
Your output must be a single, exhaustive, production-grade `architecture.md` file formatted in clean, professional Markdown.
"""

SYNTHESIS_USER_PROMPT = """
I have collected extracted architectural facts from {total_articles} engineering blog posts for the engineering team at {company_name}.

YOUR TASK:
Synthesize all the provided JSON facts into a cohesive, structured architectural blueprint document titled `architecture.md`.

HOW TO PROCESS & RESOLVE THE DATA:
1. ENTITY DEDUPLICATION & ALIAS RESOLUTION:
   - Combine variations of entity names into one canonical section (e.g., merge "Zuul", "Zuul 2", and "Zuul Gateway" into "Zuul API Gateway").
   - Explicitly document technology evolutions (e.g., "System A was deprecated in favor of System B in 2022").

2. DOMAIN CLUSTERING:
   - Group entities dynamically based on their `domain_tags` into high-level architectural domains.
   - Typical domains include (but are not limited to): Edge & Traffic Management, Core Platform & Microservices, Data Storage & Caching, Event Streaming & Data Pipelines, ML & AI Infrastructure, Observability & Reliability.

3. TOPOLOGY DIAGRAM GENERATION:
   - Create a complete, valid `Mermaid.js` flowchart (using ```mermaid ... ``` code block) near the top of the file.
   - Map out how major subsystems interconnect using the extracted `system_integrations` data.

4. DETAILED DOMAIN BREAKDOWN:
   - For each domain, list every core system, its purpose, tech stack, design patterns, and connections to other domains.

DOCUMENT FORMAT TEMPLATE TO FOLLOW:

# {company_name} Platform Architecture Blueprint

## 1. High-Level System Topology
```mermaid
graph TD
  %% Mermaid diagram mapping core data paths and service relationships
```

## 2. Architectural Domains

### [Domain Name, e.g., Edge & Traffic Routing]
**Overview**: [Brief overview of this domain's role in the organization]

**Key Systems & Services**:

#### [Canonical Service Name]
- **Purpose**: [What it does]
- **Tech Stack**: [Language/Frameworks/Protocols]
- **Design Patterns**: [e.g., Circuit Breakers, Async Proxying]
- **Interactions**: [Integrates with System X, feeds data to System Y]
- **Evolution & Legacy Status**: [Active / Replaced / Upgraded from X]

## 3. Cross-Cutting Design Patterns & Infrastructure Trends
[Summary of company-wide architectural principles observed across posts, such as Multi-Cloud resiliency, Monorepo strategies, or Zero-Trust security].

EXTRACTED FACTS DATA:
{facts_json_data}
"""

BATCH_SYNTHESIS_SYSTEM_PROMPT = """
You are a Principal Enterprise Architect. Summarize a batch of extracted engineering-blog
architectural facts into a compact intermediate summary that a later synthesis pass will
merge with other batch summaries. Preserve entity names, tech stacks, integrations, and
evolution notes faithfully — do not drop detail for the sake of brevity beyond deduplication
within this batch.
"""

BATCH_SYNTHESIS_USER_PROMPT = """
Summarize the following {article_count} articles' worth of extracted architectural facts for
{company_name} into a single consolidated JSON object with the same shape as one extraction
result (`primary_entities`, `system_integrations`, `evolution_notes`), deduplicating entities
by `canonical_name` within this batch.

FACTS DATA:
{facts_json_data}
"""
