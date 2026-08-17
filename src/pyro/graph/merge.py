"""Graph-merge pass: each extracted article's entities/relationships (extract/pipeline.py's
output) get folded into company_name's company-wide entity graph, replacing the old prose
synthesis stage (docs/architecture.md, "The layers" — Graph merge).

The one LLM call per article shows the model a flat list of the company's existing entity
*names* (cheap — see Database.list_entity_names) plus this article's own extracted entities, and
asks it to decide, per entity, whether it's the same system as an existing one (reuse that exact
name) or new (keep the article's own name). That resolution is the only thing the merge prompt
decides — kind/domain/relationships all come straight from extraction, unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from json_repair import repair_json
from litellm import acompletion

from pyro.config import Settings
from pyro.db import Article, Database
from pyro.graph.prompts import merge_system_prompt, merge_user_prompt
from pyro.router import graph_model_params, stream_with_rate_limit_retry

logger = logging.getLogger(__name__)


class GraphReporter:
    """Progress hook for a graph-merge run's individual LLM calls. No-op by default (CLI/cron
    usage); api/jobs.py supplies a Job-backed subclass so the dashboard can stream each call's
    output live and keep it as history after the run finishes."""

    def start_call(self, label: str, model: str) -> None:
        pass

    def on_chunk(self, content: str, reasoning: str) -> None:
        pass

    def end_call(self, error: str | None = None) -> None:
        pass


@dataclass
class GraphMergeContext:
    """The values every call in a merge run shares — bundled so functions take one param
    instead of company_name/settings/model_params/reporter separately each time."""

    company_name: str
    settings: Settings
    model_params: dict
    reporter: GraphReporter = field(default_factory=GraphReporter)


async def _resolve_names(
    article_entities: list[dict],
    existing_names: list[str],
    article_title: str,
    ctx: GraphMergeContext,
) -> dict[str, str]:
    """One LLM call: returns {article_entity_name: canonical_name} for every entity this
    article extracted."""
    model_params = {**ctx.model_params, "max_tokens": ctx.settings.graph_max_tokens}
    ctx.reporter.start_call(label=article_title, model=model_params["model"])
    entities_json = json.dumps(
        [
            {"name": e["name"], "kind": e.get("kind"), "domain": e.get("domain")}
            for e in article_entities
        ],
        indent=2,
    )
    existing_names_block = "\n".join(f"- {n}" for n in existing_names) or "(none yet)"
    try:
        raw, _reasoning = await stream_with_rate_limit_retry(
            lambda: acompletion(
                messages=[
                    {"role": "system", "content": merge_system_prompt(ctx.settings)},
                    {
                        "role": "user",
                        "content": merge_user_prompt(ctx.settings).format(
                            company_name=ctx.company_name,
                            existing_entity_names=existing_names_block,
                            title=article_title,
                            article_entities_json=entities_json,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                stream=True,
                **model_params,
            ),
            ctx.settings,
            ctx.reporter.on_chunk,
        )
    except Exception as exc:
        ctx.reporter.end_call(error=str(exc))
        raise
    ctx.reporter.end_call()

    parsed = repair_json(raw, return_objects=True)
    mapping: dict[str, str] = {}
    for item in parsed.get("resolved", []) if isinstance(parsed, dict) else []:
        article_name = (item.get("article_name") or "").strip()
        canonical_name = (item.get("canonical_name") or "").strip()
        if article_name and canonical_name:
            mapping[article_name] = canonical_name
    return mapping


async def _merge_article(db: Database, article: Article, ctx: GraphMergeContext) -> None:
    graph = article.extracted_graph or {}
    entities = graph.get("entities") or []
    relationships = graph.get("relationships") or []
    if not entities:
        db.mark_graph_merged(article.id)
        return

    existing_names = db.list_entity_names(ctx.company_name)
    mapping = await _resolve_names(
        entities, existing_names, article.title or article.source_url, ctx
    )

    for entity in entities:
        name = entity["name"]
        canonical_name = mapping.get(name, name)
        db.upsert_entity(
            ctx.company_name,
            canonical_name,
            entity.get("kind", "service"),
            entity.get("domain", "Other"),
            alias=name if canonical_name != name else None,
            first_seen_article_id=article.id,
        )

    for rel in relationships:
        db.upsert_relationship(
            ctx.company_name,
            mapping.get(rel["source"], rel["source"]),
            mapping.get(rel["target"], rel["target"]),
            rel["relation"],
            rel.get("as_of"),
            source_article_id=article.id,
        )

    db.mark_graph_merged(article.id)


async def run_graph_merge(
    db: Database,
    settings: Settings,
    company_name: str,
    reporter: GraphReporter | None = None,
) -> dict[str, int]:
    """Fold company_name's not-yet-merged extracted articles into the entity graph, one at a
    time (each merge call is shown entity names resolved by prior articles earlier in this run,
    so this runs sequentially, not concurrently). Already-merged articles are left untouched —
    safe to call repeatedly as new articles get extracted, without re-deciding settled names.
    Returns the company's total entity/relationship counts after this run (not just what this
    run touched) plus how many articles this run processed. Raises RuntimeError if company_name
    has no extracted articles at all; articles_merged is 0 if all extracted articles are already
    merged (nothing new to do)."""
    if not db.fetch_extracted(company_name):
        raise RuntimeError(f"no extracted articles found for {company_name!r}")

    articles = db.fetch_pending_merge(company_name)
    if settings.graph_article_limit is not None:
        articles = articles[: settings.graph_article_limit]

    ctx = GraphMergeContext(
        company_name,
        settings,
        graph_model_params(settings),
        reporter or GraphReporter(),
    )

    for article in articles:
        await _merge_article(db, article, ctx)

    return {
        "articles_merged": len(articles),
        "entities": len(db.list_entities(company_name)),
        "relationships": len(db.list_relationships(company_name)),
    }
