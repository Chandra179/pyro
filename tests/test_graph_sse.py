"""The merge-run event stream (api/sse.py).

The property under test is send-once: a delta must cross the wire exactly one time, no matter how
many times the generator samples the job. That is the whole reason this replaced the 1s poll,
which re-sent every call's entire accumulated content on every tick.
"""

import asyncio

import pytest

from api import sse
from api.jobs import GraphMergeCall, Job
from api.main import templates


class _NeverDisconnected:
    async def is_disconnected(self):
        return False


def _job(**kwargs) -> Job:
    return Job(
        id="job-1",
        company_name="Acme",
        url="https://example.com/sitemap.xml",
        limit=None,
        extraction_variant="default",
        **kwargs,
    )


async def _drain(job, timeout=5.0):
    events = []
    agen = sse.graph_history_events(_NeverDisconnected(), job, templates)

    async def _run():
        async for event in agen:
            events.append(event)

    await asyncio.wait_for(_run(), timeout=timeout)
    return events


@pytest.mark.asyncio
async def test_stream_emits_open_deltas_and_close_then_ends(monkeypatch):
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    call = GraphMergeCall(label="Scaling Titus", model="test-model")
    call.content_parts.append("hello ")
    job.graph_history.append(call)

    async def finish_soon():
        await asyncio.sleep(0.05)
        call.content_parts.append("world")
        await asyncio.sleep(0.05)
        call.done = True
        job.status = "done"

    task = asyncio.create_task(finish_soon())
    events = await _drain(job)
    await task

    kinds = [e["event"] for e in events]
    assert kinds[0] == "call-open"
    assert kinds[-1] == "stream-close"
    assert "call-close-0" in kinds

    # "hello " was already accumulated when the card opened, so it is painted *into the card* and
    # must not also arrive as a delta — only what came after does.
    card = next(e["data"] for e in events if e["event"] == "call-open")
    assert "hello " in card
    deltas = [e["data"] for e in events if e["event"] == "call-delta-0"]
    assert "".join(deltas) == "world"
    assert len(deltas) == 1


@pytest.mark.asyncio
async def test_content_present_at_open_is_not_also_sent_as_a_delta(monkeypatch):
    """Regression: the card is painted with whatever the call has accumulated so far, so seeding
    the sent-cursor at zero re-sent that prefix and hx-swap="beforeend" appended it twice —
    the card ended up holding its opening text doubled."""
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    call = GraphMergeCall(label="x", model="m")
    call.content_parts.append("A" * 150)
    call.reasoning_parts.append("R" * 40)
    job.graph_history.append(call)

    async def finish_soon():
        await asyncio.sleep(0.05)
        call.done = True
        job.status = "done"

    task = asyncio.create_task(finish_soon())
    events = await _drain(job)
    await task

    card = next(e["data"] for e in events if e["event"] == "call-open")
    deltas = "".join(e["data"] for e in events if e["event"] == "call-delta-0")
    reasoning = "".join(e["data"] for e in events if e["event"] == "call-reasoning-0")

    assert card.count("A" * 150) == 1
    # Nothing new was produced after the card opened, so nothing further should be sent.
    assert deltas == ""
    assert reasoning == ""


@pytest.mark.asyncio
async def test_each_call_gets_its_own_addressed_events(monkeypatch):
    """Three calls streaming into one container must not cross-contaminate: each card subscribes
    to events addressed to its own index."""
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    calls = [GraphMergeCall(label=f"article-{i}", model="m") for i in range(3)]

    async def produce():
        for i, call in enumerate(calls):
            job.graph_history.append(call)
            await asyncio.sleep(0.05)
            call.content_parts.append(f"body-{i}")
            await asyncio.sleep(0.05)
            call.done = True
        job.status = "done"

    task = asyncio.create_task(produce())
    events = await _drain(job)
    await task

    kinds = [e["event"] for e in events]
    assert kinds.count("call-open") == 3
    for i in range(3):
        assert f"call-close-{i}" in kinds
        deltas = "".join(e["data"] for e in events if e["event"] == f"call-delta-{i}")
        assert deltas == f"body-{i}"


@pytest.mark.asyncio
async def test_model_output_is_escaped_before_being_swapped_as_html(monkeypatch):
    """htmx inserts a frame as HTML, and model output regularly contains JSON with angle brackets
    and quotes — unescaped, it would be parsed as markup instead of shown as text."""
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    call = GraphMergeCall(label="x", model="m")
    job.graph_history.append(call)

    async def produce():
        await asyncio.sleep(0.05)
        call.content_parts.append('{"a": "<b>& \'c\'"}')
        await asyncio.sleep(0.05)
        call.done = True
        job.status = "done"

    task = asyncio.create_task(produce())
    events = await _drain(job)
    await task

    delta = "".join(e["data"] for e in events if e["event"] == "call-delta-0")
    assert "<b>" not in delta
    assert "&lt;b&gt;" in delta
    assert "&amp;" in delta


@pytest.mark.asyncio
async def test_a_failed_calls_error_is_streamed_on_close(monkeypatch):
    """The error only exists once the call ends, so the card was painted without it — it has to
    arrive as its own frame or the failure shows as a bare "failed" badge with no reason."""
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    call = GraphMergeCall(label="x", model="m")
    job.graph_history.append(call)

    async def produce():
        await asyncio.sleep(0.05)
        call.done = True
        call.error = "rate limited after 5 retries"
        job.status = "error"

    task = asyncio.create_task(produce())
    events = await _drain(job)
    await task

    kinds = [e["event"] for e in events]
    assert kinds.index("call-error-0") < kinds.index("call-close-0")
    by_event = {e["event"]: e["data"] for e in events}
    assert "rate limited after 5 retries" in by_event["call-error-0"]
    assert "failed" in by_event["call-close-0"]


@pytest.mark.asyncio
async def test_a_successful_call_sends_no_error_frame(monkeypatch):
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    call = GraphMergeCall(label="x", model="m", done=True)
    job.graph_history.append(call)
    job.status = "done"

    events = await _drain(job)
    assert not any(e["event"].startswith("call-error") for e in events)


@pytest.mark.asyncio
async def test_stream_stops_when_the_client_goes_away(monkeypatch):
    """A job that never finishes must not hold the generator open once the browser is gone."""
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)

    class _Disconnected:
        async def is_disconnected(self):
            return True

    job = _job(status="merging")
    job.graph_history.append(GraphMergeCall(label="x"))

    events = []
    async for event in sse.graph_history_events(_Disconnected(), job, templates):
        events.append(event)
    assert events == []


@pytest.mark.asyncio
async def test_open_frame_carries_the_calls_delta_subscriptions(monkeypatch):
    """The card the stream sends must wire up its own sse-swap targets, or subsequent deltas have
    nowhere to land."""
    monkeypatch.setattr(sse, "_SAMPLE_INTERVAL_S", 0.01)
    job = _job(status="merging")
    job.graph_history.append(GraphMergeCall(label="Scaling Titus", model="m", done=True))
    job.status = "done"

    events = await _drain(job)
    card = next(e["data"] for e in events if e["event"] == "call-open")
    assert 'sse-swap="call-delta-0"' in card
    assert 'hx-swap="beforeend"' in card
    assert 'sse-swap="call-close-0"' in card
    assert "Scaling Titus" in card
