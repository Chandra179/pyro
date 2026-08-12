# Engineering Blog Architecture Synthesis — Design Plan

## Overview

Modern tech giants publish detailed engineering blogs explaining how they build massive, distributed systems. However, this knowledge is heavily fragmented across hundreds of individual posts published over many years.

While this blueprint is initially tuned to process the **Netflix TechBlog**, the pipeline is designed as a **General Architecture Framework**. The scraping, extraction, and synthesis engine is built to ingest any company's technical blog (e.g., Uber, Airbnb, Meta, Stripe) and output a unified, production-grade `architecture.md` map.

## Goals

- **Automated Aggregation**: Programmatically scrape, clean, and store articles from targeted engineering blogs without getting blocked by client-side rendering or anti-bot checks.
- **Generic Semantic Extraction**: Use LLM prompts designed with loose schemas to distill lengthy blog posts into structured facts (components, domains, tech stacks, and integration points) without hardcoding company-specific terminology.
- **Holistic System Synthesis**: Automatically resolve system evolution, cluster functional domains, and synthesize individual facts into a single, cohesive `architecture.md` file complete with a Mermaid.js topology flowchart.

## 1. The Multi-Stage Data Pipeline

### Raw Phase (`sitemap.xml` + Playwright → SQLite)

- **Sitemap XML**: Gives you the master list of URLs cleanly without needing to write a complex, recursive web crawler.
- **Playwright HTML/DOM**: Solves Medium and custom blog client-side rendering/lazy-loading issues by fetching the fully constructed DOM exactly as a user sees it.
- **SQLite Storage**: Keeps your raw text safe locally. By indexing and deduplicating at the database layer (using unique post identifiers), you ensure you never waste money or compute re-processing the same article twice.

### Cleaning & Chunking Phase (Raw DOM → Normalized Text)

- **Boilerplate Stripping**: Before extraction, strip nav/header/footer/sidebar, "related posts," and comment sections from the raw DOM (target `<article>`/`<main>`, or use a readability-style extraction library). This removes noise that costs tokens and can confuse the extraction model.
- **Code Block Normalization**: Collapse large code samples to a placeholder or short summary — architecture extraction needs "they use gRPC," not the full proto file. Full code blocks mostly add token cost without adding architectural signal.
- **Chunking for Outlier Posts**: For posts exceeding a token threshold (e.g., >8k tokens), split into overlapping sections and run extraction per chunk, then merge the resulting fact lists for that article before storing — dedup entities within the same article by `canonical_name`.

### Extraction Phase (SQLite → Free-Form JSON Facts)

- **Flexible Entity Resolution**: By using loose tags, entity names, and functional descriptions instead of strict string keys, you prevent the LLM from hallucinating or breaking. When one post says "Zuul Gateway" and another says "Zuul L7 Router," the flexible schema captures both naturally without forcing an immediate dictionary key match.
- **Non-Architectural Articles**: If `is_architectural` is `false`, keep the row in SQLite (flagged `is_architectural = false`) but exclude it from the Synthesis phase input query — no need to discard the extraction you already paid for, just don't feed it downstream.

### Synthesis Phase (All Facts → `architecture.md`)

- **Global Context Window**: Passing all extracted facts into a single high-reasoning prompt lets the AI act as an enterprise architect. It can independently:
  - Cluster domains
  - Resolve naming duplicates
  - Identify superseded legacy tech (e.g., Hystrix yielding to Resilience4j for Netflix, or Ribbon yielding to gRPC)
  - Build an accurate Mermaid.js topological flowchart automatically

## 2. Pro-Tips for Generalizing Beyond Netflix

### URL ID Regex Normalization

When deduplicating URLs in SQLite:

- **Medium-hosted blogs** (like Netflix): extract the 12-character hex ID at the end of the path
  (e.g., `modeling-device-capabilities-for-analytics-e7607acebde8` → `e7607acebde8`)
- **Non-Medium blogs** (e.g., Uber/Stripe): strip query parameters and use normalized URL path slugs as the database key.

### Domain-Agnostic Extraction

In your extraction prompts, avoid mentioning specific company tools (don't write "Look for Zuul or EVCache"). Keep the prompt generalized to capture "primary components, infrastructure tools, databases, and dependencies." This allows you to point the exact same Python script at `eng.uber.com` or [stripe.com/blog/engineering](https://stripe.com/blog/engineering) by simply changing the `sitemap.xml` seed URL.

### Batching for Large-Scale Blogs

If a company's blog contains 500+ articles, don't pre-group batches by `domain_tags` — those tags are assigned independently per article by the extraction LLM (e.g., "Storage" vs. "Data Storage" vs. `["Storage", "NoSQL"]`), so grouping by raw tag string scatters what should be one cluster across multiple buckets without a normalization pass first.

Instead:

1. Batch by raw article count or token budget (e.g., 50 articles/batch), and run an intermediate synthesis pass per batch to produce a batch-level summary.
2. Execute a final synthesis pass that merges all batch summaries into the master `architecture.md` — domain clustering happens once, in this final pass, where the model can see all batch summaries together.

This is simpler than tag-canonicalization and sufficient until article counts get large enough (roughly 1000+) that the final merge pass itself becomes too large for a single context window — at that point, consider adding a canonicalization pass that maps all distinct `domain_tags` to a fixed set of ~8-12 canonical domains before batching.

## 3. Prompt Templates

### Prompt 1: Generic Fact Extraction

**Purpose**: Process one raw article at a time from SQLite.

**Key Characteristic**: Uses a loose Pydantic schema. It does not assume any pre-known technologies or rigid keys, capturing names, tags, interactions, and design patterns dynamically.

```python
EXTRACTION_SYSTEM_PROMPT = """
You are a Staff Technical Architect built for analyzing enterprise software engineering blogs.
Your goal is to extract clear, accurate architectural facts from a raw blog post without making assumptions or inventing facts not present in the text.
"""

EXTRACTION_USER_PROMPT = """
Analyze the following technical blog post and extract its core architectural concepts into structured JSON.

ARTICLE METADATA:
- Title: {title}
- Source URL: {url}

ARTICLE CONTENT:
{content}

EXTRACTION INSTRUCTIONS:
1. `is_architectural` (boolean): Set to `true` IF AND ONLY IF the article discusses software systems, databases, infrastructure, network patterns, or data pipelines. Set to `false` for posts about company culture, hiring, news, or general event announcements.
2. `primary_entities` (list of objects): Identify all major components, frameworks, services, databases, or platforms introduced or detailed in the post.
   For each entity extract:
   - `canonical_name`: The complete, official name of the tool or service (e.g., "Zuul API Gateway", "Apache Pinot", "M3 Metrics Store"). Normalize variations to the full canonical name.
   - `domain_tags`: Flexible functional categories describing what the tool does (e.g., ["Edge", "Routing"], ["Storage", "NoSQL"], ["ML Platform", "Inference"]).
   - `description`: 2-3 concise sentences on what this entity does, why it was created, and its core capabilities.
   - `tech_stack`: Any underlying frameworks, languages, hardware, or protocols mentioned (e.g., ["Java", "Netty", "gRPC", "eBPF"]).
   - `patterns_and_concepts`: Key software design patterns or architectural concepts used (e.g., ["Circuit Breaker", "CQRS", "Consistent Hashing", "Sharding"]).
3. `system_integrations` (list of objects): Explicit relationships or data flows between components mentioned in this article.
   - `source`: The originating entity/system.
   - `target`: The receiving or integrated entity/system.
   - `relationship_type`: How they interact (e.g., "reads from", "streams events to", "proxies requests to", "deprecates/replaces").
4. `evolution_notes` (list of strings): Note each system replacement, migration, or upgrade described in this article — a single article can describe multiple evolutions (e.g., ["Replaced legacy monolith with microservice mesh", "Migrated from Postgres to MySQL"]). Return an empty list if none are described.

Return strict, valid JSON conforming to these instructions.
"""
```

### Prompt 2: Global Architecture Synthesis

**Purpose**: Run once across the combined array of JSON facts extracted from all articles.

**Key Characteristic**: Acts as a Principal Enterprise Architect to deduplicate naming variations, cluster components into functional domain sections, and produce a Mermaid.js topology diagram alongside a consolidated `architecture.md`.

```python
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
```

**Why this pair works for any company**:

1. **Extraction prompt is completely vendor-agnostic**: It looks for universal concepts (`primary_entities`, `tech_stack`, `system_integrations`, `evolution_notes`) rather than company-specific terms.
2. **Synthesis prompt accepts a dynamic `{company_name}` variable**: You can pass `"Netflix"`, `"Uber"`, or `"Stripe"` at runtime, and the prompt automatically adjusts headers and domain naming while merging facts correctly.

## 4. Model Routing: The "Free Provider First" Model

An in-depth, production-ready breakdown of how free-tier routing works under the hood, how to implement structured JSON outputs across free providers, how to manage rate limits (429s) gracefully, and how to write Python code that seamlessly cascades from free models to paid fallbacks.

### Understanding the Free Tier Ecosystem

When scraping and processing hundreds of blog posts, API costs can add up quickly if you send raw HTML directly to paid models like `gpt-4o` or `claude-3-5-sonnet`. By using OpenRouter or direct developer free tiers (like Google AI Studio), you can run 100% of your Pass 1 (Extraction) for free, saving your paid API credits strictly for Pass 2 (Synthesis).

### Comparison of Free LLM Options for Scraping Pipelines

| Provider | Free Models Available | Core Strengths | Key Constraints / Limits | Best Used For |
|---|---|---|---|---|
| **OpenRouter (Free Tier)** | `google/gemini-2.5-flash:free`, `meta-llama/llama-3.3-70b-instruct:free`, `qwen/qwen-2.5-72b-instruct:free` | Aggregates 20+ free models under one unified OpenAI-compatible API. Auto-load balancing. | 20 requests/minute (RPM) limit across free tier. Context windows vary by host. | Primary Extraction Pipeline (single API interface for all free models). |
| **Google AI Studio (Direct API)** | `gemini-2.5-flash`, `gemini-2.5-pro` | Massive context window (1M–2M tokens). Native JSON schema support. | 15 RPM / 1,000 RPD (Requests Per Day) on Flash. Data used to train models (free tier policy). | Secondary extraction fallback & high-context parsing. |
| **Groq Cloud (Direct API)** | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768` | Extreme inference speed (500+ tokens/sec). Native JSON mode. | Strict Tokens Per Minute (TPM) limits on large prompts. Lower daily request caps on free tier. | Fast entity extraction on short articles. |

### Core Architectural Challenges with Free Tiers (And How to Solve Them)

Running an extraction pipeline on free LLM endpoints introduces three main friction points:

1. **Strict 429 Rate Limits (RPM/TPM Limits)**: Free models hit 429 errors quickly when loops fire off requests in parallel.
2. **Schema Compliance Instability**: Smaller or community-hosted free models (like Llama 70B or Qwen 72B) may occasionally output Markdown backticks or plain text instead of strict JSON.
3. **Endpoint Downtime**: Free providers on OpenRouter can experience brief outages or temporary capacity exhaustion (503 Service Unavailable).

### The Solution: The Cascading Fallback Architecture

Instead of failing when a model hits a 429 or returns malformed JSON, your pipeline should implement a Cascading Fallback Ring:

```
[ Raw Article Text ]
        │
        ▼
[ 1. OpenRouter: gemini-2.5-flash:free ] ──(200 OK & Valid JSON)──► [ Return Fact ]
        │ (429 Rate Limit / 503 Outage / Invalid JSON)
        ▼
[ 2. OpenRouter: llama-3.3-70b-instruct:free ] ──(200 OK)───────► [ Return Fact ]
        │ (429 Rate Limit / 503 Outage / Invalid JSON)
        ▼
[ 3. Direct Google AI Studio Key (Gemini Flash) ] ──────────────► [ Return Fact ]
        │ (Daily Quota Exhausted)
        ▼
[ 4. Paid Fallback: OpenAI gpt-4o-mini / DeepSeek V3 ] ─────────► [ Return Fact ]
```

### Implementation: LiteLLM Router

Rather than hand-rolling this cascade logic (custom `try/except` chains, manual RPM/TPM tracking, retry backoff), use **[LiteLLM](https://github.com/BerriAI/litellm)** as the orchestration layer. LiteLLM exposes a unified OpenAI-compatible interface across 100+ providers — including OpenRouter, Google AI Studio (Gemini), Groq, and paid fallbacks like OpenAI — and its `Router` object natively implements the fallback ring above:

- **Unified interface**: Every provider/model is addressed the same way (`openrouter/...`, `gemini/...`, `groq/...`, `gpt-4o-mini`), so swapping or reordering fallback tiers is a config change, not a code change.
- **Rate-limit-aware routing**: The Router tracks RPM/TPM per deployment and automatically routes around a model that's near its limit instead of waiting for a 429.
- **Automatic retries & cooldowns**: On 429 (rate limit) or 503 (outage), LiteLLM retries with backoff, then falls through to the next deployment in the list — the exact behavior in the diagram above.
- **Structured output support**: Native JSON mode / JSON schema enforcement across providers where supported, reducing the schema-compliance instability problem from smaller free models.

The `Router` is configured with a `model_list` covering all four tiers (OpenRouter free models → direct Google AI Studio → paid fallback), with retries and cooldowns set on rate-limit/outage errors. Pass 2 (Synthesis) can point at a single high-reasoning paid model directly, since it only runs once across the full fact set.

### Validation Layer: Handling "200 OK but Invalid JSON"

LiteLLM's Router fallback triggers on HTTP-level failures (429, 503, timeouts) — it does **not** know if a `200 OK` response contains malformed or schema-invalid JSON. Since smaller/community-hosted free models (Llama 70B, Qwen 72B via OpenRouter) can return valid HTTP responses with broken JSON (e.g., wrapped in Markdown backticks, missing required fields), the Router alone won't route around that failure mode.

This needs a separate validation layer that treats a Pydantic parse failure the same as a provider error: iterate the concrete model list directly (rather than relying on the Router's internal fallback state, which only advances on raised exceptions) and validate each response against the extraction schema before accepting it, moving to the next model on any validation failure. This closes the gap between "the HTTP call succeeded" and "the output is actually usable" — the two failure modes (provider errors vs. schema-invalid output) are complementary, not redundant, and both need to be handled for the pipeline to be reliable at free-tier scale.

## 5. Operational Details

### SQLite Schema

A minimal `articles` table backs the Raw/Cleaning/Extraction phases:

- `id` (the normalized URL key — Medium hex ID or path slug per §2)
- `source_url`, `title`, `company_name`
- `raw_html`, `cleaned_text` (post-boilerplate-stripping, from the Cleaning phase)
- `is_architectural` (nullable boolean, set after extraction)
- `extracted_facts` (JSON, the Pydantic-validated extraction output)
- `scraped_at`, `extracted_at` (timestamps, used to skip already-processed rows on re-runs)

`id` is the primary key, enforcing the dedup guarantee described in the Raw Phase.

### Concurrency

Extraction (Pass 1) runs many articles independently, so it's a natural candidate for concurrent requests — but concurrency must stay bounded against the free-tier RPM/TPM ceilings in the provider table in §4 (e.g., 20 RPM on OpenRouter's free tier), not just handled per-call via retries. Use a bounded async worker pool (e.g., `asyncio.Semaphore` sized to the active tier's RPM limit) rather than firing all article requests at once — otherwise most requests will hit 429s immediately and burn through the fallback cascade before any legitimate rate-limited capacity is used.

### Success Criteria Before Scaling Up

Before running the pipeline across a full blog (hundreds of articles), validate on a small sample first:

1. Run Raw → Cleaning → Extraction on ~10 articles spanning different post types (deep architecture post, short announcement, culture/hiring post) and manually confirm `is_architectural` and extracted facts are accurate.
2. Run Synthesis on that sample's facts and confirm the output `architecture.md` renders correctly, including a valid Mermaid diagram.
3. Only after both pass, scale to the full article set with batching (§2) enabled.
