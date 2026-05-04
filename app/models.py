from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, enum.Enum):
    """Phases of a single remediation effort on one GitHub issue.

    The lifecycle follows the user's mental model:

        pending
          ↓
        planning  ⇄  plan_posted          ─┐
          │                                 │  iteration on the plan stays
          ↓ (`@devin go ahead`)             │  in this band
        remediating  ⇄  awaiting_user     ─┘
          ↓
        pr_opened
          ↓               ↓                 ↓
        done       closed_unmerged    closed_unfixed
        (merged)    (PR rejected)      (issue closed
                                        without a PR)

    `failed` is reachable from any phase on a system error.

    A task starts in `planning` if the first comment is a plan request,
    otherwise in `remediating` (skipping any "session_started" interlude
    — Devin is already running by the time we set the status).
    """

    PENDING = "pending"
    PLANNING = "planning"
    PLAN_POSTED = "plan_posted"
    REMEDIATING = "remediating"
    AWAITING_USER = "awaiting_user"
    PR_OPENED = "pr_opened"
    DONE = "done"
    CLOSED_UNMERGED = "closed_unmerged"
    CLOSED_UNFIXED = "closed_unfixed"
    FAILED = "failed"


ACTIVE_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.PLANNING.value,
    TaskStatus.PLAN_POSTED.value,
    TaskStatus.REMEDIATING.value,
    TaskStatus.AWAITING_USER.value,
    TaskStatus.PR_OPENED.value,
}

TERMINAL_STATUSES = {
    TaskStatus.DONE.value,
    TaskStatus.CLOSED_UNMERGED.value,
    TaskStatus.CLOSED_UNFIXED.value,
    TaskStatus.FAILED.value,
}

# Phases where Devin is producing planning text and the orchestrator
# should treat `@devin go ahead` as a transition to remediation.
PLAN_PHASE_STATUSES = {
    TaskStatus.PLANNING.value,
    TaskStatus.PLAN_POSTED.value,
}

# Phases where Devin is actively writing code (or just finished and is
# awaiting human review). Same-mode follow-ups flow through to the
# session as iteration on the implementation.
REMEDIATE_PHASE_STATUSES = {
    TaskStatus.REMEDIATING.value,
    TaskStatus.AWAITING_USER.value,
    TaskStatus.PR_OPENED.value,
}


class RemediationTask(Base):
    __tablename__ = "remediation_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), index=True)
    issue_number: Mapped[int] = mapped_column(Integer, index=True)
    issue_title: Mapped[str] = mapped_column(String(1024), default="")
    issue_url: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(64), default=TaskStatus.PENDING.value, index=True)
    devin_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    last_github_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_devin_update_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    time_to_pr_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_to_completion_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Newline-separated list of PR URLs that were superseded by user-requested
    # replans. Populated when a `pr_opened` task transitions back to
    # `planning` so the dashboard can show prior attempts.
    previous_pr_urls: Mapped[str | None] = mapped_column(Text, nullable=True)

    events: Mapped[list["InteractionEvent"]] = relationship(
        "InteractionEvent",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="InteractionEvent.id",
    )

    __table_args__ = (
        Index("ix_repo_issue", "repo_full_name", "issue_number"),
    )


class ProcessedComment(Base):
    """Cross-process dedupe for incoming GitHub `issue_comment` deliveries.

    The PK on `github_comment_id` makes the claim INSERT atomic across
    workers and replicas: concurrent attempts to handle the same GitHub
    comment id race for the row, and the loser bails with IntegrityError
    before any Devin/GitHub API calls. The in-process per-issue lock is
    a latency optimization on top of this primitive — the DB constraint
    is what makes the dedupe correct under multi-process deployments.

    Comments without an id (e.g. simulate-comment calls that omit it)
    skip the claim entirely.
    """

    __tablename__ = "processed_comments"

    github_comment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class InteractionEvent(Base):
    __tablename__ = "interaction_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("remediation_tasks.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(32))  # github | devin | orchestrator
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    github_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    github_comment_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    task: Mapped[RemediationTask] = relationship("RemediationTask", back_populates="events")
