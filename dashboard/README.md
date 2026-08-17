# pyro dashboard

htmx + Jinja2 + Tailwind web UI for triggering pipeline runs and browsing what's already been
extracted. For the concept-level picture (what the pipeline does, why the dashboard streams
instead of polls, known limitations), see [`docs/architecture.md`](../docs/architecture.md#dashboard)
— this file stays one level down: how the dashboard itself is put together and how to work on it.

Run it with `make dashboard` (requires `db-up` and `OPENROUTER_API_KEY`; see the root
[README](../README.md)).

## Layers

The dashboard is two halves that only communicate over HTTP — nothing here is imported by the
other:

- **`api/`** (repo root, not under this directory) — a FastAPI app. Routes render full pages or
  Jinja partials, and one route (`/jobs/{id}/graph-events`) streams server-sent events. It's the
  only code here that touches the pipeline or the database — `dashboard/` itself has zero Python.
  - `api/main.py` — routes and the `/data` view's context-building
  - `api/jobs.py` — in-memory job store; runs scrape → clean → extract → merge-graph on a
    background thread per submitted job
  - `api/sse.py` — the merge-run event stream a running job's page subscribes to
  - `api/graph_view.py` — builds Mermaid flowchart source from a company's stored entity graph
  - `api/render.py` — wraps Mermaid source in the markup mermaid.js scans for client-side
  - `api/deps.py` — the request-scoped `Database`/`Settings` dependencies routes use
- **`dashboard/`** (this directory) — templates and static assets only, no logic:
  - `templates/` — Jinja2, extending `base.html`; see below
  - `static/js/app.js` — sidebar/dark-mode/mermaid/modal/SSE-autoscroll behavior; the one thing
    that has to stay inline in `base.html` is the dark-mode bootstrap script, so it runs before
    first paint
  - `static/src/input.css` → `static/css/app.css` — Tailwind v4 source and its compiled,
    committed-in-git output (see **CSS workflow** below)

## Template structure

```
base.html                        sidebar, header, theme toggle, script includes
├── index.html                   "Runs" page: new-run form + recent-runs list
│   └── partials/job_status.html   one run's card (self-polls its summary every 2s until finished)
│       └── partials/graph_history.html   that run's merge-call history
│           └── partials/graph_call.html      one LLM call (streamed live via SSE, or static once finished)
│               └── partials/graph_call_status.html   that call's status badge
└── data.html                    "Data" page
    └── partials/data_shell.html   company picker + extraction/graph tabs + preview modal
        └── partials/data_panel.html   dispatcher: db-error / no-companies / extraction / graph
            ├── partials/_panel_extraction.html   article table (self-polls every 4s)
            │   └── partials/article_modal.html      row "View" swaps this into the preview modal
            └── partials/_panel_graph.html        one rendered Mermaid diagram, no self-poll

partials/_macros.html            shared `badge()` macro (status/stage pills)
```

Two rendering patterns worth knowing before touching either page:

- **Runs page**: a job card is rendered once and never swapped wholesale — only its summary block
  self-polls. That's deliberate: the SSE extension attaches a listener per `sse-swap` element with
  no re-registration guard, so re-swapping the card while merge history streams underneath it
  double-appends every subsequent chunk (see the comment in `job_status.html` for how this was
  found).
- **Data page**: the shell (company picker + tabs) is swapped by a full `/data` navigation with
  `hx-select` plucking the shell back out, so the pushed URL and the active tab state always come
  from the same server render. The panel underneath it re-polls itself independently on a short
  interval via `/data/panel`, without touching the shell or the URL.

## CSS workflow

`static/css/app.css` is generated from `static/src/input.css` and is committed — the FastAPI app
serves the compiled file directly and has no build step of its own. After editing a template's
classes or `input.css`, regenerate it:

```bash
npm install       # once
npm run build:css # or: npm run watch:css
```