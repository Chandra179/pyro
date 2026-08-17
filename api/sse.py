"""Server-sent events for a graph-merge run's streamed LLM output.

This replaces a 1-second htmx poll of `/jobs/{id}/graph-history`, which re-rendered *every* call's
*entire* accumulated content on every tick — so the response grew with the run, and a 50-article
merge re-sent the whole transcript once a second until it finished. api/jobs.py went to some
trouble to keep chunk accumulation O(n) rather than O(n^2); the transport then threw that away.

Here each byte crosses the wire exactly once. The stream emits three kinds of event:

  - `call-open`            a new call's card, appended to the history list
  - `call-delta-{i}`       text appended to call i's content (and `call-reasoning-{i}` likewise)
  - `call-close-{i}`       call i's final status badge

The producer (`JobGraphReporter`) runs on a plain background thread with no reference to this
event loop, so the generator samples the job's in-memory state on a short interval and diffs it
against what it has already sent, rather than being pushed to via a queue. That keeps the
cross-thread story simple while preserving the send-once property: sampling costs a list read, not
a database query, a template render, and an HTTP round trip.
"""

from __future__ import annotations

import asyncio
import html
from collections.abc import AsyncIterator
from types import SimpleNamespace

from fastapi import Request
from fastapi.templating import Jinja2Templates

from api.jobs import GraphMergeCall, Job

# How often the generator samples job state. Fast enough to read as live, and cheap because a
# sample that finds no new bytes yields nothing at all.
_SAMPLE_INTERVAL_S = 0.25


def _freeze(call: GraphMergeCall) -> SimpleNamespace:
    """An immutable point-in-time copy of a call, for rendering. `content`/`reasoning` are
    properties over lists the job thread appends to, so reading one twice can return two different
    strings; a frozen copy makes "what the card was painted with" and "how much has been sent"
    provably the same value."""
    return SimpleNamespace(
        label=call.label,
        model=call.model,
        done=call.done,
        error=call.error,
        content=call.content,
        reasoning=call.reasoning,
    )


def _render(templates: Jinja2Templates, name: str, context: dict) -> str:
    """Render a partial to a string. SSE frames carry markup, not a Response, so this goes
    through the template environment directly rather than through TemplateResponse."""
    return templates.get_template(name).render(**context)


async def graph_history_events(
    request: Request, job: Job, templates: Jinja2Templates
) -> AsyncIterator[dict]:
    opened = 0
    sent_content: dict[int, int] = {}
    sent_reasoning: dict[int, int] = {}
    closed: set[int] = set()

    while True:
        if await request.is_disconnected():
            return

        # One snapshot per pass: the list is mutated from the job thread, and re-reading it
        # between the loops below could otherwise see a call appear mid-iteration.
        history = list(job.graph_history)

        while opened < len(history):
            index = opened
            # Render the card from a frozen copy, and seed the cursors from that same copy. The
            # card template paints whatever the call has accumulated so far, so a call that
            # already produced output by the time its card opens carries that text in the card
            # itself; seeding the cursor from zero would re-send exactly that prefix as a delta,
            # and hx-swap="beforeend" would append it a second time. Freezing rather than reading
            # call.content twice also closes the window where the producer thread appends between
            # the measurement and the render, which would duplicate the overlap instead.
            frozen = _freeze(history[index])
            yield {
                "event": "call-open",
                "data": _render(
                    templates,
                    "partials/graph_call.html",
                    {"job": job, "call": frozen, "index": index},
                ),
            }
            sent_content[index] = len(frozen.content)
            sent_reasoning[index] = len(frozen.reasoning)
            opened += 1

        for index, call in enumerate(history):
            for attr, sent, event in (
                (call.content, sent_content, "call-delta"),
                (call.reasoning, sent_reasoning, "call-reasoning"),
            ):
                already = sent.get(index, 0)
                if len(attr) > already:
                    sent[index] = len(attr)
                    # Escaped because htmx inserts the frame as HTML; model output is arbitrary
                    # text and regularly contains JSON with angle brackets and quotes.
                    yield {
                        "event": f"{event}-{index}",
                        "data": html.escape(attr[already:]),
                    }

            if call.done and index not in closed:
                closed.add(index)
                # A call's error only exists once it ends, so the card was painted without it.
                # Sent before the badge so the message is on screen by the time the status flips.
                if call.error:
                    yield {
                        "event": f"call-error-{index}",
                        "data": html.escape(call.error),
                    }
                yield {
                    "event": f"call-close-{index}",
                    "data": _render(
                        templates, "partials/graph_call_status.html", {"call": call}
                    ),
                }

        # Only stop once the job is over *and* every call it produced has been fully drained —
        # the final chunks of the last call can land after status flips to done.
        if job.is_finished and closed == set(range(len(history))):
            yield {"event": "stream-close", "data": ""}
            return

        await asyncio.sleep(_SAMPLE_INTERVAL_S)
