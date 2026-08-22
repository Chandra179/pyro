"""Graph-merge pass: each extracted article's entities/relationships get folded into
company_name's company-wide entity graph (docs/architecture.md, "The layers" — Graph merge).

Resolution runs in two tiers: a deterministic pass (graph/resolve.py) matches entity names by
exact/fuzzy string match; only names it can't settle reach the LLM, shown the unresolved entities
plus the most-similar existing names. That resolution is the only thing the merge prompt decides —
kind/domain/relationships all come straight from extraction, unchanged.
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
    """One entry of the merge call's raw JSON response — distinct from graph.resolve.ResolvedName."""

    article_name: str
    canonical_name: str


class ResolutionResponse(BaseModel):
    """The merge call's expected JSON shape, modelled so a malformed response fails in one place."""

    resolved: list[ResolvedNameItem] = []


class GraphReporter:
    """Progress hook for a graph-merge run's individual LLM calls. No-op by default; api/jobs.py
    supplies a Job-backed subclass so the dashboard can stream each call's output live."""

    def start_call(self, label: str, model: str) -> None:
        pass

    def on_chunk(self, content: str, reasoning: str) -> None:
        pass

    def report_summary(
        self,
        new_entities: int,
        matched_entities: int,
        relationships_count: int,
        llm_resolved_count: int,
    ) -> None:
        """Reported once, after upsert, deliberately before `end_call` — so a subclass that flips
        a "done" UI state on `end_call` never observes a done call with no summary attached."""

    def end_call(self, error: str | None = None) -> None:
        pass


@dataclass
class GraphMergeContext:
    """The values every call in a merge run shares."""

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
    "llm" even when the model decides an entity is new — still a judgment call, worth
    distinguishing from a match nobody had to reason about."""
    model_params = {**ctx.model_params, "max_tokens": ctx.settings.graph_max_tokens}
    ctx.reporter.start_call(label=article_title, model=model_params["model"])
    kinds = {e["name"]: e for e in article_entities}
    entities_json = json.dumps(
        [
            {
                "name": name,
                "kind": kinds.get(name, {}).get("kind"),
                "domain": kinds.get(name, {}).get("domain"),
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
    # Not `ctx.reporter.end_call()` here: _merge_article still upserts and reports a summary
    # before this call should read as finished — see report_summary's docstring.
    try:
        parsed = ResolutionResponse.model_validate(
            repair_json(raw, return_objects=True)
        )
    except ValidationError:
        # Must not abort the run: an empty mapping treats every unresolved entity as new, which
        # over-counts rather than corrupting existing ones, and a later re-merge can still fold them.
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
    # Gates report_summary/end_call below so a fully-deterministic article stays invisible in
    # the dashboard's merge history, same as before it had summaries at all.
    had_call = bool(unresolved)
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
        # Deterministic matches win — letting the model reassign a name it was never asked about
        # would silently undo them.
        mapping = {**llm_mapping, **mapping}
    else:
        logger.debug(
            "all %d entities resolved deterministically for %s; skipping merge call",
            len(entities),
            article.id,
        )

    # Snapshot before upserting so "new" means new-to-the-graph, not new within this loop.
    known_before = set(known.names)
    new_count = 0
    matched_count = 0
    llm_resolved_count = sum(1 for r in mapping.values() if r.method == "llm")

    for entity in entities:
        name = entity["name"]
        resolved = mapping.get(name)
        canonical_name = resolved.canonical if resolved else name
        if canonical_name in known_before:
            matched_count += 1
        else:
            new_count += 1
            known_before.add(canonical_name)
        db.upsert_entity(
            ctx.company_name,
            canonical_name,
            entity.get("kind", "service"),
            entity.get("domain", "Other"),
            alias=name if canonical_name != name else None,
            alias_method=resolved.method if resolved and canonical_name != name else None,
            first_seen_article_id=article.id,
            description=entity.get("description"),
        )
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
            # source_name was decommissioned; close its outgoing edges' validity window (see
            # db/relationships.py's invalidate_outgoing). Edges pointing *at* it stay valid.
            db.invalidate_outgoing_relationships(
                ctx.company_name,
                source_name,
                at=rel.get("as_of") or now_iso(),
                exclude_relation="replaced_by",
            )

    if had_call:
        ctx.reporter.report_summary(
            new_entities=new_count,
            matched_entities=matched_count,
            relationships_count=len(relationships),
            llm_resolved_count=llm_resolved_count,
        )
        ctx.reporter.end_call()

    db.mark_graph_merged(article.id)


async def run_graph_merge(
    db: Database,
    settings: Settings,
    company_name: str,
    reporter: GraphReporter | None = None,
) -> dict[str, int]:
    """Fold company_name's not-yet-merged extracted articles into the entity graph, one at a time
    — sequential, since each call needs to see names resolved by prior articles this run. Returns
    total entity/relationship counts after the run plus how many articles were processed. Raises
    RuntimeError if company_name has no extracted articles at all; articles_merged is 0 if
    everything's already merged."""
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
