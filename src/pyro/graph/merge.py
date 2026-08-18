"""Graph-merge pass: each extracted article's entities/relationships (extract/pipeline.py's
output) get folded into company_name's company-wide entity graph, replacing the old prose
synthesis stage (docs/architecture.md, "The layers" — Graph merge).

Resolution runs in two tiers. A deterministic pass (graph/resolve.py) matches this article's
entity names against the company's existing ones by exact and fuzzy string match; only the names
it can't settle reach the LLM, which is shown those unresolved entities plus the existing names
most similar to them, and asked to decide per entity whether it's the same system as an existing
one (reuse that exact name) or new (keep the article's own name). An article whose entities all
resolve deterministically costs no model call at all.

That resolution is the only thing the merge prompt decides — kind/domain/relationships all come
straight from extraction, unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from json_repair import repair_json
from litellm import acompletion
from pydantic import BaseModel, ValidationError

from pyro.config import Settings
from pyro.db import Article, Database
from pyro.db.keys import now_iso
from pyro.graph.prompts import merge_system_prompt, merge_user_prompt
from pyro.graph.resolve import KnownNames, ResolvedName, candidate_names
from pyro.router import graph_model_params, stream_with_rate_limit_retry

logger = logging.getLogger(__name__)


class ResolvedNameItem(BaseModel):
    """One entry of the merge call's raw JSON response — distinct from graph.resolve.ResolvedName
    (the canonical/method pair the rest of this module works with) to avoid the two colliding."""

    article_name: str
    canonical_name: str


class ResolutionResponse(BaseModel):
    """The merge call's expected JSON shape. Modelled rather than dict-walked so a malformed
    response fails in one place with a useful error, matching how extract/schema.py already
    validates the extraction call's output."""

    resolved: list[ResolvedNameItem] = []


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
    unresolved: list[str],
    article_entities: list[dict],
    existing_names: list[str],
    article_title: str,
    ctx: GraphMergeContext,
) -> dict[str, ResolvedName]:
    """One LLM call for the entities the deterministic pass couldn't settle: returns
    {article_entity_name: ResolvedName(canonical_name, method="llm")} for each. `method` is
    always "llm" here even when the model decides an entity is new (canonical == article's own
    name) — it's still a model judgment call, worth distinguishing later from a match nobody ever
    had to reason about (see ResolvedName's docstring)."""
    model_params = {**ctx.model_params, "max_tokens": ctx.settings.graph_max_tokens}
    ctx.reporter.start_call(label=article_title, model=model_params["model"])
    kinds = {e["name"]: e for e in article_entities}
    entities_json = json.dumps(
        [
            {
                "name": name,
                "kind": kinds.get(name, {}).get("kind"),
                "domain": kinds.get(name, {}).get("domain"),
                # Most load-bearing for a generic name like "new microservice" — kind/domain alone
                # rarely distinguish it from an unrelated system with the same generic phrasing.
                "description": kinds.get(name, {}).get("description"),
            }
            for name in unresolved
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

    try:
        parsed = ResolutionResponse.model_validate(
            repair_json(raw, return_objects=True)
        )
    except ValidationError:
        # A malformed merge response must not abort the run: leaving the mapping empty means every
        # unresolved entity keeps the article's own name, i.e. is treated as new. That over-counts
        # entities rather than corrupting existing ones, and a later re-merge can still fold them.
        logger.warning("merge response failed validation for %r; treating all as new", article_title)
        return {}

    return {
        item.article_name.strip(): ResolvedName(
            canonical=item.canonical_name.strip(), method="llm"
        )
        for item in parsed.resolved
        if item.article_name.strip() and item.canonical_name.strip()
    }


async def _merge_article(
    db: Database, article: Article, ctx: GraphMergeContext, known: KnownNames
) -> None:
    graph = article.extracted_graph or {}
    entities = graph.get("entities") or []
    relationships = graph.get("relationships") or []
    if not entities:
        db.mark_graph_merged(article.id)
        return

    mapping, unresolved = known.resolve(
        [e["name"] for e in entities],
        threshold=ctx.settings.graph_fuzzy_threshold,
    )
    if unresolved:
        llm_mapping = await _resolve_names(
            unresolved,
            entities,
            candidate_names(
                unresolved, known.names, limit=ctx.settings.graph_candidate_names_limit
            ),
            article.title or article.source_url,
            ctx,
        )
        # Deterministic matches win: they are exact/near-exact string evidence, and letting the
        # model reassign a name it was never asked about would silently undo them.
        mapping = {**llm_mapping, **mapping}
    else:
        logger.debug(
            "all %d entities resolved deterministically for %s; skipping merge call",
            len(entities),
            article.id,
        )

    for entity in entities:
        name = entity["name"]
        resolved = mapping.get(name)
        canonical_name = resolved.canonical if resolved else name
        db.upsert_entity(
            ctx.company_name,
            canonical_name,
            entity.get("kind", "service"),
            entity.get("domain", "Other"),
            alias=name if canonical_name != name else None,
            # Only meaningful alongside a real alias: a resolved-but-unchanged name (LLM said
            # "new") or a missing mapping (malformed merge response) both leave canonical == name,
            # so alias is already None above and there is no decision to record.
            alias_method=resolved.method if resolved and canonical_name != name else None,
            first_seen_article_id=article.id,
            description=entity.get("description"),
        )
        # So the next article in this run sees it too, without a re-fetch from the DB.
        known.add(canonical_name)

    def _canonical(name: str) -> str:
        resolved = mapping.get(name)
        return resolved.canonical if resolved else name

    for rel in relationships:
        source_name = _canonical(rel["source"])
        target_name = _canonical(rel["target"])
        db.upsert_relationship(
            ctx.company_name,
            source_name,
            target_name,
            rel["relation"],
            rel.get("as_of"),
            source_article_id=article.id,
            relation_phrase=rel.get("relation_phrase"),
        )
        if rel["relation"] == "replaced_by":
            # source_name is the system this article says was replaced by target_name — its own
            # outgoing edges (calls/writes_to/...) describe behavior that stopped once it was
            # decommissioned, so close their validity window (see db/relationships.py's
            # invalidate_outgoing). The replaced_by fact itself, and anything pointing *at*
            # source_name, stays valid — those remain historically true regardless.
            db.invalidate_outgoing_relationships(
                ctx.company_name,
                source_name,
                at=rel.get("as_of") or now_iso(),
                exclude_relation="replaced_by",
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
    known = KnownNames(db.list_entity_names(company_name))

    for article in articles:
        await _merge_article(db, article, ctx, known)

    return {
        "articles_merged": len(articles),
        "entities": len(db.list_entities(company_name)),
        "relationships": len(db.list_relationships(company_name)),
    }
