"""Extraction prompts, verbatim from plan.md 'Prompt 1: Generic Fact Extraction'."""

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
