from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _stamp_utc(value):
    """Datetimes round-trip through SQLite as tz-naive even when stored as
    `DateTime(timezone=True)`. Stamp UTC on the way out so Pydantic emits
    `…+00:00` in the JSON payload and the React dashboard's
    `new Date(iso).toLocaleString()` correctly converts to the user's
    local time zone (not the raw stored UTC numbers)."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class SimulateCommentRequest(BaseModel):
    repo_full_name: str
    issue_number: int
    issue_title: str = ""
    issue_url: str = ""
    issue_body: str = ""
    comment_body: str
    comment_author: str = "demo-user"
    comment_url: Optional[str] = None
    github_comment_id: Optional[int] = None


class SendMessageRequest(BaseModel):
    message: str


class TaskOut(BaseModel):
    id: int
    repo_full_name: str
    issue_number: int
    issue_title: str
    issue_url: str
    status: str
    devin_session_id: Optional[str]
    devin_session_url: Optional[str]
    pr_url: Optional[str]
    requested_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_github_comment_id: Optional[int]
    last_devin_update_at: Optional[datetime]
    time_to_pr_seconds: Optional[int]
    time_to_completion_seconds: Optional[int]
    error: Optional[str]
    trigger_source: Optional[str] = None
    # Latest InteractionEvent metadata, surfaced on the list view so the
    # "Latest interaction" column reflects nudges/transitions even when
    # the task's status hasn't changed.
    last_event_type: Optional[str] = None
    last_event_source: Optional[str] = None
    last_event_at: Optional[datetime] = None
    # PRs that were superseded by user-requested replans; surfaced in the
    # task detail view so reviewers can find prior attempts.
    previous_pr_urls: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, validate_assignment=True)

    @field_validator(
        "created_at",
        "updated_at",
        "last_devin_update_at",
        "last_event_at",
        mode="before",
    )
    @classmethod
    def _ensure_utc(cls, v):
        return _stamp_utc(v)

    @field_validator("previous_pr_urls", mode="before")
    @classmethod
    def _split_previous_prs(cls, v):
        # Stored as newline-separated text on the model; emit as list[str].
        if v is None or v == "":
            return []
        if isinstance(v, list):
            return v
        return [line for line in str(v).splitlines() if line.strip()]


class EventOut(BaseModel):
    id: int
    task_id: int
    source: str
    event_type: str
    github_comment_id: Optional[int]
    github_comment_url: Optional[str]
    body: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, validate_assignment=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v):
        return _stamp_utc(v)


class TaskDetailOut(BaseModel):
    task: TaskOut
    events: list[EventOut]


class MetricsOut(BaseModel):
    total_devin_mentions: int
    total_sessions: int
    active_sessions: int
    awaiting_user: int
    awaiting_review: int = 0
    prs_opened: int
    completed_tasks: int
    done_no_change: int = 0
    failed_tasks: int
    closed_without_fix: int = 0
    followups_forwarded: int = 0
    unique_issues: int = 0
    unique_requesters: int = 0
    average_time_to_pr_minutes: Optional[float] = Field(default=None)
    average_time_to_completion_minutes: Optional[float] = Field(default=None)


class HealthOut(BaseModel):
    webhook_ready: bool
    devin_configured: bool
    github_configured: bool
    database_ok: bool
