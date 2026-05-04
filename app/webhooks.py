from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db, get_devin_client, get_github_client, get_settings
from app.github_client import verify_signature
from app.orchestrator import (
    comment_mentions_devin,
    handle_comment_event,
    handle_issue_closed,
    handle_pr_closed,
)

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    db: Session = Depends(get_db),
    devin=Depends(get_devin_client),
    gh=Depends(get_github_client),
    settings=Depends(get_settings),
):
    raw = await request.body()
    if settings.github_webhook_secret:
        sig = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(settings.github_webhook_secret, raw, sig):
            raise HTTPException(status_code=401, detail="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")

    SUPPORTED = {"issue_comment", "issues", "pull_request"}
    if event not in SUPPORTED:
        return {"action": "ignored", "reason": "unsupported_event", "event": event}

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    repo_full_name = (payload.get("repository") or {}).get("full_name") or ""
    if not repo_full_name:
        return {"action": "ignored", "reason": "missing_repo"}

    # ─── Issue closed / reopened ──────────────────────────────────────────
    if event == "issues":
        action = payload.get("action")
        if action != "closed":
            return {"action": "ignored", "reason": "non_closed_issues_action", "received": action}
        issue = payload.get("issue") or {}
        issue_number = int(issue.get("number") or 0)
        lock = request.app.state.issue_locks.for_issue(repo_full_name, issue_number)
        async with lock:
            return await asyncio.to_thread(
                handle_issue_closed,
                db,
                gh,
                repo_full_name=repo_full_name,
                issue_number=issue_number,
            )

    # ─── PR closed (merged or unmerged) ───────────────────────────────────
    # No issue-level lock here: pull_request payloads carry no issue_number,
    # and the dedupe-vs-INSERT race we serialize against doesn't apply
    # (handle_pr_closed always mutates an existing task, never creates one).
    # Worst case if a comment arrives concurrently: a stray follow-up to
    # an about-to-close session — benign.
    if event == "pull_request":
        action = payload.get("action")
        if action != "closed":
            return {"action": "ignored", "reason": "non_closed_pr_action", "received": action}
        pr = payload.get("pull_request") or {}
        return await asyncio.to_thread(
            handle_pr_closed,
            db,
            gh,
            repo_full_name=repo_full_name,
            pr_url=pr.get("html_url"),
            merged=bool(pr.get("merged")),
        )

    # ─── Issue comment (the @devin trigger path) ──────────────────────────
    if payload.get("action") != "created":
        return {
            "action": "ignored",
            "reason": "non_created_action",
            "received": payload.get("action"),
        }

    issue = payload.get("issue") or {}
    if "pull_request" in issue and issue.get("pull_request") is not None:
        return {"action": "ignored", "reason": "pull_request_comment"}

    comment = payload.get("comment") or {}
    sender = (payload.get("sender") or {}).get("login") or ""
    sender_lower = sender.lower()
    bot_login = (settings.bot_github_login or "").strip().lower()
    # Two-layer loop protection:
    #   1. Any sender whose login ends with `[bot]` is a GitHub App identity
    #      (e.g. our orchestrator's own App). Always filter.
    #   2. Optional `BOT_GITHUB_LOGIN` for the legacy single-user-account
    #      case where the bot identity isn't suffixed with `[bot]`.
    if sender_lower.endswith("[bot]") or (bot_login and sender_lower == bot_login):
        return {"action": "ignored", "reason": "bot_comment"}

    body = comment.get("body") or ""
    if not comment_mentions_devin(body):
        return {"action": "ignored", "reason": "no_devin_mention"}

    # Run the orchestrator on a worker thread so the slow Devin/GitHub I/O
    # inside it doesn't pin the event loop. The per-issue lock serializes
    # deliveries to the same (repo, issue) so the idempotency check can't
    # race the task INSERT — critical when GitHub retries a slow webhook
    # and both attempts arrive within the same second. Different issues
    # parallelize.
    issue_number = int(issue.get("number") or 0)
    lock = request.app.state.issue_locks.for_issue(repo_full_name, issue_number)
    async with lock:
        return await asyncio.to_thread(
            handle_comment_event,
            db,
            devin,
            gh,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            issue_title=issue.get("title") or "",
            issue_url=issue.get("html_url") or "",
            issue_body=issue.get("body") or "",
            comment_body=body,
            comment_author=(comment.get("user") or {}).get("login") or sender,
            comment_url=comment.get("html_url"),
            github_comment_id=comment.get("id"),
            rate_limit_sessions_per_hour=settings.rate_limit_sessions_per_hour,
        )
