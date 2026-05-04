from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api import router as api_router
from app.config import Settings
from app.database import Database
from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.poller import poll_forever
from app.webhooks import router as webhook_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class IssueLocks:
    """Per-(repo, issue_number) async locks.

    Different issues parallelize; same-issue webhooks (e.g. GitHub's
    retry of a slow first attempt) serialize. Locks are created on
    first request and accumulate across the process lifetime — fine
    for the demo's scale; production would want TTL eviction.

    Safe under the asyncio single-threaded event-loop model: dict
    operations are not interleaved with awaits inside this class.
    """

    def __init__(self) -> None:
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}

    def for_issue(self, repo_full_name: str, issue_number: int) -> asyncio.Lock:
        key = (repo_full_name, int(issue_number or 0))
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


def create_app(
    devin_client=None,
    github_client=None,
    settings: Optional[Settings] = None,
) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_url)
    db.create_all()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task: Optional[asyncio.Task] = None
        if app.state.settings.poller_enabled:
            task = asyncio.create_task(
                poll_forever(app, app.state.settings.poller_interval_seconds)
            )
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(title="Devin GitHub Issue Orchestrator", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    # Per-(repo, issue_number) locks. Concurrent webhook deliveries to the
    # *same* issue serialize through one lock so the dedupe check cannot
    # race the task INSERT (GitHub retries are the realistic source).
    # Different issues can be processed in parallel — important when a
    # busy repo gets a burst of @devin mentions across many issues.
    app.state.issue_locks = IssueLocks()
    app.state.devin_client = devin_client or DevinClient(
        api_key=settings.devin_api_key,
        org_id=settings.devin_org_id,
        base_url=settings.devin_api_base,
    )
    private_key = settings.github_app_private_key
    if not private_key and settings.github_app_private_key_path:
        from pathlib import Path
        p = Path(settings.github_app_private_key_path)
        if p.exists():
            private_key = p.read_text()
    app.state.github_client = github_client or GitHubClient(
        token=settings.github_token,
        app_id=settings.github_app_id,
        private_key=private_key,
        base_url=settings.github_api_base,
    )

    app.include_router(webhook_router)
    app.include_router(api_router)

    @app.get("/", response_class=HTMLResponse)
    def root():
        # The dashboard is served by the React + Vite container in
        # docker-compose at http://localhost:5173. This endpoint documents
        # the FastAPI backend's HTTP surface; the auto-generated OpenAPI UIs
        # at /docs and /redoc are the interactive deep-dive.
        dashboard_url = "http://localhost:5173"
        return HTMLResponse(
            f"""<!doctype html>
<html><head><meta charset="utf-8" />
<title>Devin GitHub Issue Orchestrator — API</title>
<style>
:root{{--bg:#0f1115;--card:#161922;--border:#232838;--mute:#8a93a6;--text:#e8ecf3;
--accent:#5b8cff;--get:#3fb950;--post:#d29922;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}}
*{{box-sizing:border-box}}
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:48px 32px;line-height:1.55}}
.wrap{{max-width:920px;margin:0 auto}}
h1{{margin:0 0 8px;font-size:28px}}
h2{{margin:32px 0 12px;font-size:18px;color:var(--text);border-bottom:1px solid var(--border);
padding-bottom:8px}}
p{{margin:0 0 12px;color:var(--mute)}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
code{{font-family:var(--mono);background:#1c2030;padding:2px 6px;border-radius:4px;font-size:13px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px;
margin:0 0 16px}}
.links{{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 0}}
.links a{{background:#1c2030;border:1px solid var(--border);padding:6px 12px;border-radius:6px;
font-size:13px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{color:var(--mute);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.method{{font-family:var(--mono);font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px;
display:inline-block;letter-spacing:.04em}}
.method.get{{background:rgba(63,185,80,.15);color:var(--get)}}
.method.post{{background:rgba(210,153,34,.15);color:var(--post)}}
.path{{font-family:var(--mono);font-size:13px;color:var(--text)}}
.desc{{color:var(--mute);font-size:13px}}
.muted{{color:var(--mute);font-size:13px}}
</style></head><body>
<div class="wrap">
<h1>Devin GitHub Issue Orchestrator</h1>
<p>FastAPI control plane for the <code>@devin</code> → Devin session pipeline. Below is every HTTP endpoint the backend exposes. For an interactive try-it-out UI, see the auto-generated OpenAPI viewers.</p>

<div class="card">
  <strong>Surfaces</strong>
  <div class="links">
    <a href="{dashboard_url}">Dashboard ({dashboard_url})</a>
    <a href="/docs">Swagger UI (/docs)</a>
    <a href="/redoc">ReDoc (/redoc)</a>
    <a href="/openapi.json">OpenAPI JSON</a>
    <a href="/healthz">Liveness (/healthz)</a>
  </div>
</div>

<h2>Dashboard read API</h2>
<p>Read-only endpoints the React dashboard polls to render metrics, the task list, and per-task timelines.</p>
<table>
  <thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead>
  <tbody>
    <tr>
      <td><span class="method get">GET</span></td>
      <td class="path">/api/metrics</td>
      <td class="desc">Aggregate dashboard metrics — total mentions, active sessions, PRs merged, closed without fix, errors, avg time-to-PR, avg time-to-completion, unique issues, unique requesters. Computed live from <code>remediation_tasks</code>.</td>
    </tr>
    <tr>
      <td><span class="method get">GET</span></td>
      <td class="path">/api/tasks</td>
      <td class="desc">List every task, newest first, with the most recent <code>InteractionEvent</code> denormalized onto each row so the list view can show "follow-up forwarded" / "phase transition" without a per-row query.</td>
    </tr>
    <tr>
      <td><span class="method get">GET</span></td>
      <td class="path">/api/tasks/{{task_id}}</td>
      <td class="desc">Full task detail plus the ordered <code>interaction_events</code> stream — the per-task timeline the dashboard renders.</td>
    </tr>
    <tr>
      <td><span class="method get">GET</span></td>
      <td class="path">/api/health</td>
      <td class="desc">Backend connectivity check — reports webhook readiness, Devin/GitHub credential presence, and a live DB ping. Used by the dashboard's diagnostics card.</td>
    </tr>
  </tbody>
</table>

<h2>Orchestration write API</h2>
<p>Endpoints that mutate task state. The <code>simulate-comment</code> endpoint runs the <em>same</em> orchestrator code path as a real GitHub webhook — only the transport differs.</p>
<table>
  <thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead>
  <tbody>
    <tr>
      <td><span class="method post">POST</span></td>
      <td class="path">/api/simulate-comment</td>
      <td class="desc">Trigger the orchestrator with a synthetic <code>@devin</code> comment. Hits <code>handle_comment_event</code> exactly like the webhook does (per-issue lock + worker thread). Used by the dashboard's Demo &amp; diagnostics panel and by the test suite.</td>
    </tr>
    <tr>
      <td><span class="method post">POST</span></td>
      <td class="path">/api/tasks/{{task_id}}/send</td>
      <td class="desc">Forward a message from the dashboard into the existing Devin session. Records a <code>user_instruction</code> event on the timeline.</td>
    </tr>
    <tr>
      <td><span class="method post">POST</span></td>
      <td class="path">/api/tasks/{{task_id}}/refresh</td>
      <td class="desc">Force an immediate poll-and-reconcile against Devin for one task (calls <code>refresh_task_from_devin</code>). Returns the updated task + events. Useful when the user wants the dashboard to update without waiting 45s.</td>
    </tr>
  </tbody>
</table>

<h2>Inbound webhooks</h2>
<p>External-event entry points. HMAC-SHA256 verified against <code>GITHUB_WEBHOOK_SECRET</code> when configured.</p>
<table>
  <thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead>
  <tbody>
    <tr>
      <td><span class="method post">POST</span></td>
      <td class="path">/webhooks/github</td>
      <td class="desc">GitHub webhook receiver. Handles <code>issue_comment</code> (the <code>@devin</code> trigger), <code>issues.closed</code> (→ <code>closed_unfixed</code>), and <code>pull_request.closed</code> (→ <code>done</code> or <code>closed_unmerged</code>). Bot senders are filtered.</td>
    </tr>
  </tbody>
</table>

<h2>Liveness</h2>
<table>
  <thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead>
  <tbody>
    <tr>
      <td><span class="method get">GET</span></td>
      <td class="path">/healthz</td>
      <td class="desc">Static <code>{{"ok": true}}</code>. Lightweight liveness probe. <code>/api/health</code> is the richer readiness check.</td>
    </tr>
    <tr>
      <td><span class="method get">GET</span></td>
      <td class="path">/</td>
      <td class="desc">This page.</td>
    </tr>
  </tbody>
</table>

<p class="muted" style="margin-top:32px">Bring it up with <code>docker compose up --build</code>. Source: <code>app/api.py</code>, <code>app/webhooks.py</code>, <code>app/main.py</code>.</p>
</div>
</body></html>"""
        )

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
