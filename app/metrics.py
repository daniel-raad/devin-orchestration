from __future__ import annotations

from typing import Optional

from sqlalchemy import String, func
from sqlalchemy.orm import Session

from app.models import (
    ACTIVE_STATUSES,
    InteractionEvent,
    RemediationTask,
    TaskStatus,
)


def _avg_minutes(db: Session, column) -> Optional[float]:
    avg_seconds = db.query(func.avg(column)).filter(column.isnot(None)).scalar()
    if avg_seconds is None:
        return None
    return round(float(avg_seconds) / 60.0, 2)


def compute_metrics(db: Session) -> dict:
    total_mentions = (
        db.query(func.count(InteractionEvent.id))
        .filter(InteractionEvent.event_type == "user_instruction")
        .scalar()
        or 0
    )
    total_sessions = db.query(func.count(RemediationTask.id)).scalar() or 0

    active_sessions = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.status.in_(list(ACTIVE_STATUSES)))
        .scalar()
        or 0
    )

    awaiting_user = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.status == TaskStatus.AWAITING_USER.value)
        .scalar()
        or 0
    )

    prs_opened = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.pr_url.isnot(None))
        .scalar()
        or 0
    )

    # "PRs merged" specifically — DONE tasks that actually shipped a PR.
    # DONE-without-pr_url is a legitimate terminal outcome (Devin completed
    # without changes) but is not a merge.
    completed_tasks = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.status == TaskStatus.DONE.value)
        .filter(RemediationTask.pr_url.isnot(None))
        .scalar()
        or 0
    )

    done_no_change = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.status == TaskStatus.DONE.value)
        .filter(RemediationTask.pr_url.is_(None))
        .scalar()
        or 0
    )

    failed_tasks = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.status == TaskStatus.FAILED.value)
        .scalar()
        or 0
    )

    closed_without_fix = (
        db.query(func.count(RemediationTask.id))
        .filter(
            RemediationTask.status.in_(
                [TaskStatus.CLOSED_UNMERGED.value, TaskStatus.CLOSED_UNFIXED.value]
            )
        )
        .scalar()
        or 0
    )

    awaiting_review = (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.status == TaskStatus.PR_OPENED.value)
        .scalar()
        or 0
    )

    followups_forwarded = (
        db.query(func.count(InteractionEvent.id))
        .filter(InteractionEvent.event_type == "followup_forwarded")
        .scalar()
        or 0
    )

    unique_issues = (
        db.query(
            func.count(
                func.distinct(
                    RemediationTask.repo_full_name + ":" + func.cast(RemediationTask.issue_number, String)
                )
            )
        ).scalar()
        or 0
    )

    unique_requesters = (
        db.query(func.count(func.distinct(RemediationTask.requested_by)))
        .filter(RemediationTask.requested_by.isnot(None))
        .scalar()
        or 0
    )

    avg_pr = _avg_minutes(db, RemediationTask.time_to_pr_seconds)
    avg_completion = _avg_minutes(db, RemediationTask.time_to_completion_seconds)

    return {
        "total_devin_mentions": int(total_mentions),
        "total_sessions": int(total_sessions),
        "active_sessions": int(active_sessions),
        "awaiting_user": int(awaiting_user),
        "awaiting_review": int(awaiting_review),
        "prs_opened": int(prs_opened),
        "completed_tasks": int(completed_tasks),
        "done_no_change": int(done_no_change),
        "failed_tasks": int(failed_tasks),
        "closed_without_fix": int(closed_without_fix),
        "followups_forwarded": int(followups_forwarded),
        "unique_issues": int(unique_issues),
        "unique_requesters": int(unique_requesters),
        "average_time_to_pr_minutes": avg_pr,
        "average_time_to_completion_minutes": avg_completion,
    }
