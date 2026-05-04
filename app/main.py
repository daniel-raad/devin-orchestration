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
        # docker-compose at http://localhost:5173. This endpoint just confirms
        # the FastAPI backend is up and points reviewers to the UI.
        dashboard_url = "http://localhost:5173"
        return HTMLResponse(
            f"""<!doctype html>
<html><head><meta charset="utf-8" />
<title>Devin GitHub Issue Orchestrator</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e8ecf3;
margin:0;padding:48px;line-height:1.5}}
h1{{margin:0 0 8px}} a{{color:#5b8cff}}
.card{{background:#161922;border:1px solid #232838;border-radius:10px;padding:20px;max-width:640px}}
code{{background:#1c2030;padding:2px 6px;border-radius:4px}}
</style></head><body>
<div class="card">
<h1>Devin GitHub Issue Orchestrator</h1>
<p>The FastAPI backend is running. The dashboard UI is a separate React app.</p>
<ul>
  <li>Dashboard: <a href="{dashboard_url}">{dashboard_url}</a></li>
  <li>API base: <code>/api/*</code></li>
  <li>Webhook: <code>POST /webhooks/github</code></li>
  <li>Health: <a href="/healthz">/healthz</a></li>
</ul>
<p class="muted">Bring it up with <code>docker compose up --build</code>.</p>
</div>
</body></html>"""
        )

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
