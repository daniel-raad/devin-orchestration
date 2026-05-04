from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import metrics as metrics_mod
from app import orchestrator
from app.deps import get_db, get_devin_client, get_github_client, get_settings
from app.models import InteractionEvent, RemediationTask
from app.schemas import (
    EventOut,
    HealthOut,
    MetricsOut,
    SendMessageRequest,
    SimulateCommentRequest,
    TaskDetailOut,
    TaskOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/metrics", response_model=MetricsOut)
def get_metrics(db: Session = Depends(get_db)) -> MetricsOut:
    data = metrics_mod.compute_metrics(db)
    return MetricsOut(**data)


@router.get("/health", response_model=HealthOut)
def get_health(
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
) -> HealthOut:
    devin_configured = bool(settings.devin_api_key and settings.devin_org_id)
    github_configured = bool(
        settings.github_token
        or (settings.github_app_id and (settings.github_app_private_key or settings.github_app_private_key_path))
    )
    database_ok = True
    try:
        db.query(RemediationTask).limit(1).all()
    except Exception:
        database_ok = False
    return HealthOut(
        webhook_ready=True,
        devin_configured=devin_configured,
        github_configured=github_configured,
        database_ok=database_ok,
    )


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db)) -> list[TaskOut]:
    from sqlalchemy import func

    rows = (
        db.query(RemediationTask)
        .order_by(RemediationTask.created_at.desc(), RemediationTask.id.desc())
        .all()
    )

    # One grouped query for the most recent InteractionEvent per task —
    # surfaces "follow-up forwarded", "phase transition", etc. on the
    # list view so the dashboard reflects nudges that don't change the
    # task's status.
    if rows:
        latest_ids_subq = (
            db.query(
                InteractionEvent.task_id.label("tid"),
                func.max(InteractionEvent.id).label("max_id"),
            )
            .filter(InteractionEvent.task_id.in_([t.id for t in rows]))
            .group_by(InteractionEvent.task_id)
            .subquery()
        )
        latest_events = (
            db.query(InteractionEvent)
            .join(latest_ids_subq, InteractionEvent.id == latest_ids_subq.c.max_id)
            .all()
        )
        latest_by_task = {e.task_id: e for e in latest_events}
    else:
        latest_by_task = {}

    out: list[TaskOut] = []
    for t in rows:
        item = TaskOut.model_validate(t)
        ev = latest_by_task.get(t.id)
        if ev is not None:
            item.last_event_type = ev.event_type
            item.last_event_source = ev.source
            item.last_event_at = ev.created_at
        out.append(item)
    return out


@router.get("/tasks/{task_id}", response_model=TaskDetailOut)
def task_detail(task_id: int, db: Session = Depends(get_db)) -> TaskDetailOut:
    task = db.get(RemediationTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    events = (
        db.query(InteractionEvent)
        .filter(InteractionEvent.task_id == task_id)
        .order_by(InteractionEvent.created_at.asc(), InteractionEvent.id.asc())
        .all()
    )
    return TaskDetailOut(
        task=TaskOut.model_validate(task),
        events=[EventOut.model_validate(e) for e in events],
    )


@router.post("/simulate-comment")
async def simulate_comment(
    payload: SimulateCommentRequest,
    request: Request,
    db: Session = Depends(get_db),
    devin=Depends(get_devin_client),
    gh=Depends(get_github_client),
    settings=Depends(get_settings),
):
    # Per-issue lock + thread-pool pattern, same as the webhook, so dashboard
    # simulate calls can't race a real webhook arriving on the same issue.
    lock = request.app.state.issue_locks.for_issue(
        payload.repo_full_name, payload.issue_number
    )
    async with lock:
        return await asyncio.to_thread(
            orchestrator.handle_comment_event,
            db,
            devin,
            gh,
            repo_full_name=payload.repo_full_name,
            issue_number=payload.issue_number,
            issue_title=payload.issue_title,
            issue_url=payload.issue_url,
            issue_body=payload.issue_body,
            comment_body=payload.comment_body,
            comment_author=payload.comment_author,
            comment_url=payload.comment_url,
            github_comment_id=payload.github_comment_id,
            trigger_source="simulated",
            rate_limit_sessions_per_hour=settings.rate_limit_sessions_per_hour,
        )


@router.post("/tasks/{task_id}/send")
def send_message(
    task_id: int,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    devin=Depends(get_devin_client),
):
    task = db.get(RemediationTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    result = orchestrator.send_user_message(
        db=db, devin=devin, task=task, message=payload.message
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "failed")
    return {"ok": True}


@router.post("/tasks/{task_id}/refresh", response_model=TaskDetailOut)
def refresh_task(
    task_id: int,
    db: Session = Depends(get_db),
    devin=Depends(get_devin_client),
    gh=Depends(get_github_client),
) -> TaskDetailOut:
    task = db.get(RemediationTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    orchestrator.refresh_task_from_devin(db=db, devin=devin, gh=gh, task=task)
    db.refresh(task)
    events = (
        db.query(InteractionEvent)
        .filter(InteractionEvent.task_id == task_id)
        .order_by(InteractionEvent.created_at.asc(), InteractionEvent.id.asc())
        .all()
    )
    return TaskDetailOut(
        task=TaskOut.model_validate(task),
        events=[EventOut.model_validate(e) for e in events],
    )
