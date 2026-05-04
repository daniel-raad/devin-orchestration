"""Core orchestration logic for the @devin GitHub workflow.

The orchestrator is the single funnel for both the GitHub webhook and the
in-product simulation endpoint. It decides whether to create a new Devin
session, forward a follow-up, or ignore.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ACTIVE_STATUSES,
    PLAN_PHASE_STATUSES,
    REMEDIATE_PHASE_STATUSES,
    TERMINAL_STATUSES,
    InteractionEvent,
    ProcessedComment,
    RemediationTask,
    TaskStatus,
)
from app.modes import PLAN, Mode, detect_mode, get_mode, mode_for_status
from app.prompts import build_plan_to_remediate_prompt, build_replan_from_pr_prompt

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utc(d: datetime) -> datetime:
    """SQLite rebinds tz-aware datetimes as naive on round-trip; coerce both
    sides to naive UTC before any comparison or subtraction."""
    if d.tzinfo is None:
        return d
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def _seconds_between(later: datetime, earlier: datetime) -> int:
    return max(int((_naive_utc(later) - _naive_utc(earlier)).total_seconds()), 0)


def _is_strictly_after(later: datetime, earlier: datetime) -> bool:
    """Sub-second-aware ordering. _seconds_between int-truncates and clamps
    to 0, which can't distinguish "later by 100ms" from "exactly equal" —
    that breaks freshness checks within a single test/poll cycle."""
    return _naive_utc(later) > _naive_utc(earlier)


def comment_mentions_devin(body: str | None) -> bool:
    return bool(body) and "@devin" in (body or "").lower()


def is_retry_request(body: str | None) -> bool:
    """True for phrases that should advance to the *next* phase — either
    creating a new session on a terminal task, or transitioning a
    plan-phase task into remediating.

    Includes literal retries, plan→remediate continuations, and explicit
    "now remediate" phrasings.
    """
    text = (body or "").lower()
    return any(
        k in text
        for k in (
            "retry",
            "continue",
            "redo",
            "try again",
            "reopen",
            "go ahead",
            "implement",
            "build it",
            "ship it",
            "do it",
            "let's do it",
            "proceed",
            "remediate",
            "remediating",
        )
    )


def _sessions_created_in_last_hour(db: Session, repo_full_name: str) -> int:
    cutoff = _utcnow() - timedelta(hours=1)
    # SQLite rebinds tz-aware datetimes as naive; keep both sides naive UTC.
    cutoff_naive = cutoff.replace(tzinfo=None)
    return (
        db.query(func.count(RemediationTask.id))
        .filter(RemediationTask.repo_full_name == repo_full_name)
        .filter(RemediationTask.created_at >= cutoff_naive)
        .scalar()
        or 0
    )


_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def _pr_number_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = _PR_NUMBER_RE.search(url)
    return int(m.group(1)) if m else None


def _safe_post(gh, *, repo_full_name: str, issue_number: int, body: str) -> Optional[dict]:
    try:
        return gh.post_issue_comment(
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            body=body,
        )
    except Exception as e:
        log.warning("github post_issue_comment failed: %s", e)
        return None


def _record_event(
    db: Session,
    *,
    task_id: int,
    source: str,
    event_type: str,
    body: str | None = None,
    github_comment_id: int | None = None,
    github_comment_url: str | None = None,
) -> InteractionEvent:
    ev = InteractionEvent(
        task_id=task_id,
        source=source,
        event_type=event_type,
        body=body,
        github_comment_id=github_comment_id,
        github_comment_url=github_comment_url,
    )
    db.add(ev)
    db.flush()
    return ev


def _posted_meta(posted: Optional[dict]) -> tuple[Optional[int], Optional[str]]:
    if not isinstance(posted, dict):
        return None, None
    cid = posted.get("id")
    url = posted.get("html_url") or posted.get("url")
    return cid, url


def _route(
    *,
    task: Optional[RemediationTask],
    is_plan: bool,
    is_continuation: bool,
) -> str:
    """Pure dispatcher: given the current task state and the user's intent,
    return the action key the handler should run.

    Single source of truth for "what does this comment mean given where we
    are?" — the rest of `handle_comment_event` is just ferrying arguments.

    Action keys:
      - "create_new_session"          (no task, or terminal + new intent)
      - "iterate"                     (forward as a follow-up to existing session)
      - "transition_plan_to_remediating"  (user approved the plan, do the work)
      - "transition_pr_to_planning"   (PR is up, user wants to replan)
      - "refuse_mode_switch"          (mid-flight remediation, can't switch back to plan)
      - "previous_task_done"          (terminal task, user just acknowledged)
    """
    if task is None:
        return "create_new_session"

    s = task.status

    # ── Terminal phases ─────────────────────────────────────────────────
    from app.models import TERMINAL_STATUSES as _TERM
    if s in _TERM:
        if is_plan or is_continuation:
            return "create_new_session"
        return "previous_task_done"

    # ── PR open: human review pending. Plan request = "replan from
    #    scratch", everything else = iteration on the PR.
    if s == TaskStatus.PR_OPENED.value:
        return "transition_pr_to_planning" if is_plan else "iterate"

    # ── Plan phase: plan-mode comments iterate the plan; continuation
    #    phrases like "go ahead" / "implement" approve the plan and
    #    transition to remediating.
    if s in PLAN_PHASE_STATUSES:
        if is_plan:
            return "iterate"
        if is_continuation:
            return "transition_plan_to_remediating"
        # Default for non-plan, non-continuation in plan phase: iterate.
        return "iterate"

    # ── Remediate phase (no PR yet): plan request is a backwards mid-
    #    flight switch and is refused; otherwise iterate.
    if s in REMEDIATE_PHASE_STATUSES:
        return "refuse_mode_switch" if is_plan else "iterate"

    # ── Pending / unknown: treat as iteration.
    return "iterate"


def handle_comment_event(
    db: Session,
    devin: Any,
    gh: Any,
    *,
    repo_full_name: str,
    issue_number: int,
    issue_title: str = "",
    issue_url: str = "",
    issue_body: str = "",
    comment_body: str,
    comment_author: str = "",
    comment_url: Optional[str] = None,
    github_comment_id: Optional[int] = None,
    trigger_source: str = "github_comment",
    rate_limit_sessions_per_hour: int = 0,
) -> dict:
    """Main entry point. Caller must have already filtered by event type / mention.

    But we also defensively check the @devin mention here so the simulation
    endpoint behaves identically to the webhook.
    """
    if not comment_mentions_devin(comment_body):
        return {"action": "ignored", "reason": "no_devin_mention"}

    # Idempotency claim. INSERT into `processed_comments` with PK on the
    # github_comment_id is the durable, cross-process dedupe primitive:
    # concurrent attempts to process the same comment race for the row,
    # the loser bails on IntegrityError before any Devin work happens.
    # The per-issue asyncio lock is a latency optimization on top — the
    # DB constraint is what makes the dedupe correct under multiple
    # uvicorn workers / replicas.
    if github_comment_id is not None:
        db.add(ProcessedComment(github_comment_id=github_comment_id))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(InteractionEvent)
                .filter(
                    InteractionEvent.github_comment_id == github_comment_id,
                    InteractionEvent.source == "github",
                )
                .first()
            )
            return {
                "action": "duplicate_ignored",
                "task_id": existing.task_id if existing else None,
                "reason": "duplicate_comment_id",
            }

    task = (
        db.query(RemediationTask)
        .filter(RemediationTask.repo_full_name == repo_full_name)
        .filter(RemediationTask.issue_number == issue_number)
        .order_by(RemediationTask.id.desc())
        .first()
    )

    incoming_mode = detect_mode(comment_body)
    wants_continuation = is_retry_request(comment_body)

    action = _route(
        task=task,
        is_plan=incoming_mode is PLAN,
        is_continuation=wants_continuation,
    )

    # Map action keys → handler functions. Each handler is small,
    # self-contained, and can be unit-tested independently. Adding a new
    # phase or intent is one row in `_route()` plus one entry here.
    if action == "iterate":
        return _forward_followup(
            db, devin, gh,
            task=task,
            mode=mode_for_status(task.status),
            issue_url=issue_url or task.issue_url,
            comment_body=comment_body,
            comment_author=comment_author,
            comment_url=comment_url,
            github_comment_id=github_comment_id,
        )

    if action == "transition_plan_to_remediating":
        return _transition_plan_to_remediating(
            db, devin, gh,
            task=task,
            comment_body=comment_body,
            comment_author=comment_author,
            comment_url=comment_url,
            github_comment_id=github_comment_id,
        )

    if action == "transition_pr_to_planning":
        return _transition_pr_opened_to_planning(
            db, devin, gh,
            task=task,
            comment_body=comment_body,
            comment_author=comment_author,
            comment_url=comment_url,
            github_comment_id=github_comment_id,
        )

    if action == "refuse_mode_switch":
        return _refuse_mode_switch(
            db, gh,
            task=task,
            existing_mode=mode_for_status(task.status),
            incoming_mode=incoming_mode,
            comment_body=comment_body,
            comment_url=comment_url,
            github_comment_id=github_comment_id,
        )

    if action == "previous_task_done":
        return _previous_task_done(
            db, gh,
            task=task,
            comment_body=comment_body,
            comment_url=comment_url,
            github_comment_id=github_comment_id,
        )

    # action == "create_new_session"
    if rate_limit_sessions_per_hour > 0:
        recent = _sessions_created_in_last_hour(db, repo_full_name)
        if recent >= rate_limit_sessions_per_hour:
            return _refuse_rate_limited(
                db, gh,
                repo_full_name=repo_full_name,
                issue_number=issue_number,
                limit=rate_limit_sessions_per_hour,
                comment_body=comment_body,
                comment_url=comment_url,
                github_comment_id=github_comment_id,
            )

    return _create_new_session(
        db,
        devin,
        gh,
        mode=incoming_mode,
        repo_full_name=repo_full_name,
        issue_number=issue_number,
        issue_title=issue_title,
        issue_url=issue_url,
        issue_body=issue_body,
        comment_body=comment_body,
        comment_author=comment_author,
        comment_url=comment_url,
        github_comment_id=github_comment_id,
        trigger_source=trigger_source,
    )


def _create_new_session(
    db: Session,
    devin: Any,
    gh: Any,
    *,
    mode: Mode,
    repo_full_name: str,
    issue_number: int,
    issue_title: str,
    issue_url: str,
    issue_body: str,
    comment_body: str,
    comment_author: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
    trigger_source: str = "github_comment",
) -> dict:
    task = RemediationTask(
        repo_full_name=repo_full_name,
        issue_number=issue_number,
        issue_title=issue_title or "",
        issue_url=issue_url or "",
        status=TaskStatus.PENDING.value,
        requested_by=comment_author or None,
        last_github_comment_id=github_comment_id,
        trigger_source=trigger_source,
    )
    db.add(task)
    db.flush()

    _record_event(
        db,
        task_id=task.id,
        source="github",
        event_type="user_instruction",
        body=comment_body,
        github_comment_id=github_comment_id,
        github_comment_url=comment_url,
    )

    prompt = mode.build_prompt(
        repo_full_name=repo_full_name,
        issue_title=issue_title,
        issue_url=issue_url,
        issue_body=issue_body,
        comment_body=comment_body,
    )

    try:
        result = devin.create_session(prompt=prompt, repo_full_name=repo_full_name)
    except Exception as exc:
        task.status = TaskStatus.FAILED.value
        task.error = str(exc)
        task.updated_at = _utcnow()
        db.flush()
        body = f"Devin remediation failed to start. Error: {str(exc)[:300]}"
        posted = _safe_post(
            gh, repo_full_name=repo_full_name, issue_number=issue_number, body=body
        )
        cid, url = _posted_meta(posted)
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="failed",
            body=body,
            github_comment_id=cid,
            github_comment_url=url,
        )
        db.commit()
        return {"action": "session_failed", "task_id": task.id, "error": str(exc)}

    task.devin_session_id = result.get("session_id")
    task.devin_session_url = result.get("session_url")
    # Skip the brief "session_started" interlude — the task starts directly
    # in the phase its mode implies. The poller will move it forward from
    # there as Devin makes progress.
    task.status = mode.initial_status
    task.last_devin_update_at = _utcnow()
    task.updated_at = _utcnow()
    db.flush()

    # Route the session-started ack through maybe_post_status_update so
    # that if GitHub is transiently unavailable the event is NOT recorded,
    # and the next poller cycle (or the next call here for any reason)
    # will retry. Critical for the user-visible "Devin is working / drafting"
    # acknowledgement to actually arrive.
    maybe_post_status_update(
        db=db,
        gh=gh,
        task=task,
        status_kind="session_started",
        body=mode.session_started_body(task),
    )
    db.commit()
    return {
        "action": "session_created",
        "task_id": task.id,
        "session_id": task.devin_session_id,
        "session_url": task.devin_session_url,
    }


def handle_issue_closed(
    db: Session,
    gh: Any,
    *,
    repo_full_name: str,
    issue_number: int,
) -> dict:
    """Driven by the `issues.closed` webhook. If we have an active task for
    this issue and it never reached pr_opened, mark it `closed_unfixed`."""
    task = (
        db.query(RemediationTask)
        .filter(RemediationTask.repo_full_name == repo_full_name)
        .filter(RemediationTask.issue_number == issue_number)
        .order_by(RemediationTask.id.desc())
        .first()
    )
    if task is None:
        return {"action": "ignored", "reason": "no_task_for_issue"}
    if task.status in TERMINAL_STATUSES:
        return {"action": "ignored", "reason": "already_terminal", "status": task.status}
    if task.pr_url:
        # PR exists; the pull_request webhook will decide done vs
        # closed_unmerged. Don't preempt it from here.
        return {"action": "ignored", "reason": "pr_exists", "task_id": task.id}

    previous_status = task.status
    task.status = TaskStatus.CLOSED_UNFIXED.value
    task.updated_at = _utcnow()
    if task.created_at:
        task.time_to_completion_seconds = _seconds_between(_utcnow(), task.created_at)

    body = (
        "Issue closed without a fix being shipped. Marking the orchestrator "
        "task as `closed_unfixed` for the dashboard. If you want to revisit, "
        "reopen the issue and comment `@devin` again."
    )
    maybe_post_status_update(
        db=db, gh=gh, task=task,
        status_kind="closed_unfixed",
        body=body,
    )
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="phase_transition",
        body=f"{previous_status} → closed_unfixed: issue closed without a PR",
    )
    db.commit()
    return {"action": "closed_unfixed", "task_id": task.id, "from": previous_status}


def handle_pr_closed(
    db: Session,
    gh: Any,
    *,
    repo_full_name: str,
    pr_url: str | None,
    merged: bool,
) -> dict:
    """Driven by the `pull_request.closed` webhook. Maps to `done` (merged)
    or `closed_unmerged` (closed without merging) on the matching task."""
    if not pr_url:
        return {"action": "ignored", "reason": "missing_pr_url"}

    task = (
        db.query(RemediationTask)
        .filter(RemediationTask.repo_full_name == repo_full_name)
        .filter(RemediationTask.pr_url == pr_url)
        .order_by(RemediationTask.id.desc())
        .first()
    )
    if task is None:
        return {"action": "ignored", "reason": "no_task_for_pr"}
    if task.status in TERMINAL_STATUSES:
        return {"action": "ignored", "reason": "already_terminal", "status": task.status}

    previous_status = task.status
    if merged:
        task.status = TaskStatus.DONE.value
        kind = "done"
        body = (
            f"PR merged — remediation **done**. Session: {task.devin_session_url}\n"
            f"PR: {task.pr_url}"
        )
    else:
        task.status = TaskStatus.CLOSED_UNMERGED.value
        kind = "closed_unmerged"
        body = (
            f"PR closed without merging. Marking the task `closed_unmerged` "
            f"so the dashboard distinguishes it from a successful fix.\n\n"
            f"PR: {task.pr_url}"
        )

    task.updated_at = _utcnow()
    if task.created_at:
        task.time_to_completion_seconds = _seconds_between(_utcnow(), task.created_at)

    maybe_post_status_update(db=db, gh=gh, task=task, status_kind=kind, body=body)
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="phase_transition",
        body=f"{previous_status} → {task.status}: {'PR merged' if merged else 'PR closed unmerged'}",
    )
    db.commit()
    return {
        "action": kind,
        "task_id": task.id,
        "from": previous_status,
    }


def _transition_pr_opened_to_planning(
    db: Session,
    devin: Any,
    gh: Any,
    *,
    task: RemediationTask,
    comment_body: str,
    comment_author: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
) -> dict:
    """User asked for a replan while a PR is up. Transition the task back
    to planning in place. The pr_url is cleared (and recorded in the
    interaction events as historical context). Same Devin session — we
    send a `revise the approach` message and rely on the next poll to
    pick up the new plan."""
    previous_pr = task.pr_url

    _record_event(
        db,
        task_id=task.id,
        source="github",
        event_type="user_instruction",
        body=comment_body,
        github_comment_id=github_comment_id,
        github_comment_url=comment_url,
    )
    task.last_github_comment_id = github_comment_id
    task.updated_at = _utcnow()

    message = build_replan_from_pr_prompt(
        previous_pr=previous_pr,
        comment_body=comment_body,
    )

    try:
        if task.devin_session_id:
            devin.send_message(session_id=task.devin_session_id, message=message)
    except Exception as exc:
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="error",
            body=f"Failed to forward replan-from-PR request: {exc}",
        )
        db.commit()
        return {"action": "transition_failed", "task_id": task.id, "error": str(exc)}

    previous_status = task.status
    task.status = TaskStatus.PLANNING.value
    # Append the cleared PR url to the task's previous_pr_urls ledger so
    # the dashboard can show prior attempts ("Past PRs: #6, #9").
    if previous_pr:
        prior = (task.previous_pr_urls or "").splitlines()
        if previous_pr not in prior:
            prior.append(previous_pr)
        task.previous_pr_urls = "\n".join(prior)
    # Clear the dedupe events for status comments that no longer apply, so
    # the poller can post a fresh `plan_posted` once Devin replies. The
    # `pr_opened` event stays as audit history; we just clear pr_url so
    # the lifecycle indicator reflects "back in planning".
    task.pr_url = None
    db.flush()

    # Annotate the superseded PR on GitHub so reviewers landing on it know
    # it's no longer the active attempt for this issue. Best-effort; don't
    # fail the transition if the comment can't be posted.
    if previous_pr:
        pr_number = _pr_number_from_url(previous_pr)
        if pr_number is not None:
            _safe_post(
                gh,
                repo_full_name=task.repo_full_name,
                issue_number=pr_number,
                body=(
                    f"This PR has been **superseded** by a fresh planning request "
                    f"on issue #{task.issue_number}. The orchestrator is no "
                    "longer tracking it as the active remediation attempt. "
                    "Close or merge at your discretion."
                ),
            )

    body = (
        "Got it — replanning. I asked Devin to step back and propose a "
        "different approach in this same session. The previous PR remains "
        f"on GitHub ({previous_pr or 'no recorded URL'}) and is superseded "
        "by whatever Devin proposes next. I'll post the new plan when it's ready."
    )
    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    cid, url = _posted_meta(posted)
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="phase_transition",
        body=(
            f"{previous_status} → planning: user-requested replan. "
            f"Previous PR: {previous_pr or '(none)'}"
        ),
        github_comment_id=cid,
        github_comment_url=url,
    )
    # Allow a fresh plan_posted comment on the next poll cycle.
    db.query(InteractionEvent).filter(
        InteractionEvent.task_id == task.id,
        InteractionEvent.source == "orchestrator",
        InteractionEvent.event_type == "plan_posted",
    ).delete()
    db.commit()
    return {
        "action": "phase_transition",
        "task_id": task.id,
        "from": previous_status,
        "to": task.status,
        "previous_pr": previous_pr,
    }


def _transition_plan_to_remediating(
    db: Session,
    devin: Any,
    gh: Any,
    *,
    task: RemediationTask,
    comment_body: str,
    comment_author: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
) -> dict:
    """Mutate an existing planning/plan_posted task in place into the
    remediating phase. Same task row, same Devin session — Devin gets a
    "go ahead and implement the plan" message and we update mode + status.
    """
    _record_event(
        db,
        task_id=task.id,
        source="github",
        event_type="user_instruction",
        body=comment_body,
        github_comment_id=github_comment_id,
        github_comment_url=comment_url,
    )
    task.last_github_comment_id = github_comment_id
    task.updated_at = _utcnow()

    message = build_plan_to_remediate_prompt(comment_body=comment_body)

    try:
        if task.devin_session_id:
            devin.send_message(session_id=task.devin_session_id, message=message)
    except Exception as exc:
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="error",
            body=f"Failed to forward plan→remediate handoff: {exc}",
        )
        db.commit()
        return {"action": "transition_failed", "task_id": task.id, "error": str(exc)}

    previous_status = task.status
    task.status = TaskStatus.REMEDIATING.value
    db.flush()

    body = (
        "Devin is now implementing the plan. I'll post a comment back when "
        "Devin opens a PR or asks for clarification."
    )
    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    cid, url = _posted_meta(posted)
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="phase_transition",
        body=f"{previous_status} → {task.status}: user approved the plan",
        github_comment_id=cid,
        github_comment_url=url,
    )
    db.commit()
    return {
        "action": "phase_transition",
        "task_id": task.id,
        "from": previous_status,
        "to": task.status,
    }


def _forward_followup(
    db: Session,
    devin: Any,
    gh: Any,
    *,
    task: RemediationTask,
    mode: Mode,
    issue_url: str,
    comment_body: str,
    comment_author: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
) -> dict:
    _record_event(
        db,
        task_id=task.id,
        source="github",
        event_type="user_instruction",
        body=comment_body,
        github_comment_id=github_comment_id,
        github_comment_url=comment_url,
    )
    task.last_github_comment_id = github_comment_id
    task.updated_at = _utcnow()

    prompt = mode.build_followup_prompt(
        issue_url=issue_url,
        comment_author=comment_author,
        comment_body=comment_body,
    )

    try:
        devin.send_message(session_id=task.devin_session_id, message=prompt)
    except Exception as exc:
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="error",
            body=f"Failed to forward follow-up: {exc}",
        )
        db.commit()
        return {"action": "followup_failed", "task_id": task.id, "error": str(exc)}

    # Mark this follow-up as awaiting a Devin reply, but only on
    # `pr_opened` — that's the phase where there's no other code path
    # (plan_posted, clarification_requested, pr_opened) that surfaces
    # Devin's prose back to GitHub, so chat-style replies otherwise vanish.
    # The marker is consumed by _maybe_post_followup_reply on a later poll.
    if task.status == TaskStatus.PR_OPENED.value:
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="followup_pending",
            body="(awaiting Devin reply for this follow-up)",
            github_comment_id=github_comment_id,
            github_comment_url=comment_url,
        )

    body = (
        f"Forwarded this update to the existing Devin session: {task.devin_session_url}."
    )
    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    cid, url = _posted_meta(posted)
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="followup_forwarded",
        body=body,
        github_comment_id=cid,
        github_comment_url=url,
    )
    db.commit()
    return {"action": "followup_forwarded", "task_id": task.id}


def _refuse_rate_limited(
    db: Session,
    gh: Any,
    *,
    repo_full_name: str,
    issue_number: int,
    limit: int,
    comment_body: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
) -> dict:
    """We've created `limit` sessions for this repo in the last hour. Refuse
    politely and tell the user to wait or raise the limit. The orchestrator
    does NOT create a task row for refused requests so they don't pollute
    the dashboard.
    """
    body = (
        f"Devin session creation has hit the per-repo rate limit "
        f"({limit} new sessions / hour). I won't kick off another one right "
        f"now to avoid runaway compute. Wait an hour or raise "
        f"`RATE_LIMIT_SESSIONS_PER_HOUR` in the orchestrator's environment."
    )
    _safe_post(gh, repo_full_name=repo_full_name, issue_number=issue_number, body=body)
    log.warning(
        "rate-limited new session for %s (limit=%d/hr)", repo_full_name, limit
    )
    # No DB write — this comment is a one-off refusal and there's no task to
    # attach an interaction event to. We don't dedupe via comment_id here
    # because rate-limited refusals are idempotent (post-only-once would
    # require state we don't keep), but rate-limit pressure is the bigger
    # signal anyway.
    return {
        "action": "rate_limited",
        "limit": limit,
        "reason": "sessions_per_hour_exceeded",
    }


def _refuse_mode_switch(
    db: Session,
    gh: Any,
    *,
    task: RemediationTask,
    existing_mode: Mode,
    incoming_mode: Mode,
    comment_body: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
) -> dict:
    """The active task is in mode A and the user just asked for mode B
    (e.g. active remediation gets a plan request, or vice versa). Refuse
    politely and tell the user how to resolve it, rather than silently
    handing two intents to the same Devin session.
    """
    parts = [
        f"There's already an active **{existing_mode.label}** task on this "
        f"issue (Devin session: {task.devin_session_url or 'pending'}).",
        f"You asked for **{incoming_mode.label}** instead — I'm not going to "
        "switch modes mid-flight." + existing_mode.switch_refusal_hint,
    ]
    body = "\n\n".join(parts)
    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    cid, url = _posted_meta(posted)

    _record_event(
        db,
        task_id=task.id,
        source="github",
        event_type="user_instruction",
        body=comment_body,
        github_comment_id=github_comment_id,
        github_comment_url=comment_url,
    )
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="mode_switch_refused",
        body=body,
        github_comment_id=cid,
        github_comment_url=url,
    )
    db.commit()
    return {
        "action": "mode_switch_refused",
        "task_id": task.id,
        "existing_mode": existing_mode.key,
        "requested_mode": incoming_mode.key,
    }


def _previous_task_done(
    db: Session,
    gh: Any,
    *,
    task: RemediationTask,
    comment_body: str,
    comment_url: Optional[str],
    github_comment_id: Optional[int],
) -> dict:
    parts = [f"The previous Devin session for this issue is **{task.status}**."]
    if task.devin_session_url:
        parts.append(f"Previous Devin session: {task.devin_session_url}")
    if task.pr_url:
        parts.append(f"Previous PR: {task.pr_url}")
    parts.append(
        "If you want to start a new remediation, comment `@devin retry` "
        "with any additional context."
    )
    body = "\n".join(parts)
    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    cid, url = _posted_meta(posted)

    _record_event(
        db,
        task_id=task.id,
        source="github",
        event_type="user_instruction",
        body=comment_body,
        github_comment_id=github_comment_id,
        github_comment_url=comment_url,
    )
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="status_update",
        body=body,
        github_comment_id=cid,
        github_comment_url=url,
    )
    db.commit()
    return {"action": "previous_task_complete", "task_id": task.id}


def maybe_post_status_update(
    *,
    db: Session,
    gh: Any,
    task: RemediationTask,
    status_kind: str,
    body: str,
) -> bool:
    """Post a status comment exactly once per (task, status_kind).

    Returns True if a fresh post was successfully delivered, False if
    deduped (already posted) or if the GitHub post failed.

    On post failure we deliberately do NOT record the dedupe event — the
    next poller cycle will retry the post. This is the correct trade-off
    because the status comments this dedupes (`pr_opened`, `completed`,
    `failed`, `clarification_requested`, `plan_posted`) are the
    highest-signal moments in the task lifecycle and silently dropping
    one because of a transient GitHub 5xx is the worst thing we can do.
    """
    existing = (
        db.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == status_kind,
        )
        .first()
    )
    if existing:
        return False

    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    if posted is None:
        # Transient GitHub failure. Do NOT record the dedupe event —
        # leaving it absent means the next poller iteration retries.
        return False

    cid, url = _posted_meta(posted)
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type=status_kind,
        body=body,
        github_comment_id=cid,
        github_comment_url=url,
    )
    db.commit()
    return True


def send_user_message(
    *,
    db: Session,
    devin: Any,
    task: RemediationTask,
    message: str,
) -> dict:
    """Used by /api/tasks/{id}/send to forward a message from the dashboard."""
    if not task.devin_session_id:
        return {"ok": False, "error": "no_active_devin_session"}
    try:
        devin.send_message(session_id=task.devin_session_id, message=message)
    except Exception as exc:
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="error",
            body=f"Failed to forward dashboard message: {exc}",
        )
        db.commit()
        return {"ok": False, "error": str(exc)}
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="user_instruction",
        body=message,
    )
    task.updated_at = _utcnow()
    db.commit()
    return {"ok": True}


def refresh_task_from_devin(
    *,
    db: Session,
    devin: Any,
    gh: Any,
    task: RemediationTask,
) -> RemediationTask:
    """Pull the latest session state from Devin and reconcile our task row."""
    # Backstop: if the session-started ack didn't land in the create flow
    # (e.g. GitHub returned 504 on the installation token at that moment),
    # retry it on every poll until it sticks. maybe_post_status_update
    # dedupes once it's recorded, so this is a no-op after success.
    if task.status not in TERMINAL_STATUSES:
        maybe_post_status_update(
            db=db,
            gh=gh,
            task=task,
            status_kind="session_started",
            body=mode_for_status(task.status).session_started_body(task),
        )

    try:
        snapshot = devin.get_session(task.devin_session_id) if task.devin_session_id else None
    except Exception as exc:
        _record_event(
            db,
            task_id=task.id,
            source="orchestrator",
            event_type="error",
            body=f"Failed to fetch Devin session state: {exc}",
        )
        db.commit()
        return task

    if not snapshot:
        return task

    pr_url = snapshot.get("pr_url")
    devin_status = (snapshot.get("status") or "").lower()
    error = snapshot.get("error")
    latest_message = snapshot.get("latest_message")
    now = _utcnow()

    mode = mode_for_status(task.status)
    plan_ready = bool(
        mode.response_ready is not None and mode.response_ready(snapshot, task)
    )

    new_status = task.status

    if plan_ready and task.status in PLAN_PHASE_STATUSES:
        # Plan-mode tasks: Devin reaching a settled state means the plan
        # is ready. Transition into plan_posted (NOT done) — the user can
        # still iterate or convert to remediation.
        new_status = TaskStatus.PLAN_POSTED.value
    elif devin_status in {"failed", "error", "crashed"}:
        new_status = TaskStatus.FAILED.value
    elif pr_url and task.status in REMEDIATE_PHASE_STATUSES:
        # PR exists; once it does, "done" is decided by GitHub's
        # pull_request webhook (merged) — not by Devin saying "completed".
        if task.status not in TERMINAL_STATUSES:
            new_status = TaskStatus.PR_OPENED.value
    elif devin_status in {"blocked", "awaiting_user", "waiting_user", "needs_input"}:
        # Plan-mode awaiting_user maps to plan_posted (handled above);
        # remediate-mode awaiting_user is Devin asking a code question.
        if task.status in REMEDIATE_PHASE_STATUSES:
            new_status = TaskStatus.AWAITING_USER.value
    elif devin_status in {"completed", "finished", "succeeded"}:
        # Devin reports completed without ever opening a PR. Treated as a
        # legitimate terminal outcome (e.g. Devin investigated and decided
        # no code change was needed) — NOT a failure. The "PRs merged"
        # metric is tightened to require pr_url so this case doesn't
        # inflate it; the orchestrator posts a distinct "done without a
        # PR" comment so the user sees the result.
        if not pr_url and task.status in REMEDIATE_PHASE_STATUSES:
            new_status = TaskStatus.DONE.value

    # PR url + timing — only relevant when a remediation PR opens.
    if (
        task.status in REMEDIATE_PHASE_STATUSES
        and pr_url
        and not task.pr_url
    ):
        task.pr_url = pr_url
        if task.created_at:
            task.time_to_pr_seconds = _seconds_between(now, task.created_at)
        maybe_post_status_update(
            db=db,
            gh=gh,
            task=task,
            status_kind="pr_opened",
            body=f"PR opened by Devin: {pr_url}",
        )

    if (
        plan_ready
        and task.status != TaskStatus.PLAN_POSTED.value
        and task.status in PLAN_PHASE_STATUSES
    ):
        # Post the plan body once on transition into plan_posted.
        if latest_message:
            maybe_post_status_update(
                db=db,
                gh=gh,
                task=task,
                status_kind=mode.response_event_type,
                body=mode.format_response(latest_message, task),
            )
    elif new_status == TaskStatus.FAILED.value and task.status != TaskStatus.FAILED.value:
        if error:
            task.error = error
        label = mode.label.lower()
        maybe_post_status_update(
            db=db,
            gh=gh,
            task=task,
            status_kind="failed",
            body=(
                f"Devin reported this {label} as **failed**.\n\n"
                f"Session: {task.devin_session_url}\n"
                + (f"Error: {error[:300]}" if error else "")
            ),
        )
    elif (
        new_status == TaskStatus.DONE.value
        and task.status != TaskStatus.DONE.value
        and not pr_url
    ):
        # Devin completed without ever opening a PR — distinct from a merged-PR
        # completion. Post a single status comment summarizing the outcome.
        if task.created_at:
            task.time_to_completion_seconds = _seconds_between(now, task.created_at)
        snippet = ""
        if latest_message:
            snippet = f"\n\nLatest message from Devin:\n\n> {latest_message[:500]}"
        maybe_post_status_update(
            db=db,
            gh=gh,
            task=task,
            status_kind="done_no_change",
            body=(
                "Devin completed this remediation **without opening a PR** — "
                "typically means the investigation concluded no code change "
                "was needed.\n\n"
                f"Session: {task.devin_session_url}" + snippet
            ),
        )
    elif (
        new_status == TaskStatus.AWAITING_USER.value
        and task.status != TaskStatus.AWAITING_USER.value
        and mode.response_ready is None
    ):
        # Modes that auto-post Devin's prose (response_ready is set) treat
        # awaiting_user as "the response is ready" — handled by the
        # plan_ready branch above. For modes without that hook,
        # awaiting_user means Devin has a clarification question.
        maybe_post_status_update(
            db=db,
            gh=gh,
            task=task,
            status_kind="clarification_requested",
            body=(
                "Devin needs clarification to continue."
                + (f"\n\n> {latest_message}" if latest_message else "")
                + f"\n\nReply with `@devin <answer>` on this issue."
            ),
        )

    task.status = new_status

    prior_response_body: str = ""
    if latest_message:
        # Record a single devin_response event per latest message change.
        last_devin = (
            db.query(InteractionEvent)
            .filter(
                InteractionEvent.task_id == task.id,
                InteractionEvent.source == "devin",
                InteractionEvent.event_type == "devin_response",
            )
            .order_by(InteractionEvent.id.desc())
            .first()
        )
        prior_response_body = (last_devin.body if last_devin else None) or ""
        if prior_response_body != latest_message:
            _record_event(
                db,
                task_id=task.id,
                source="devin",
                event_type="devin_response",
                body=latest_message,
            )
            task.last_devin_update_at = now

    # Surface Devin's reply to a `pr_opened` follow-up question, if it's
    # ready. No-op when no marker exists or the settle gate isn't satisfied.
    _maybe_post_followup_reply(
        db=db,
        gh=gh,
        task=task,
        latest_message=latest_message or "",
        prior_response_body=prior_response_body,
    )

    db.commit()
    return task


_FOLLOWUP_REPLY_MIN_CHARS = 60


def _maybe_post_followup_reply(
    *,
    db: Session,
    gh: Any,
    task: RemediationTask,
    latest_message: str,
    prior_response_body: str,
) -> None:
    """Settle gate for chat-style follow-up replies on `pr_opened` tasks.

    Posts Devin's `latest_message` back to the issue iff *all* of these
    hold; on any failure the marker is left open and a later poll retries.

    1. task.status is `pr_opened`. Other phases have their own posting
       paths (plan_posted, clarification_requested, pr_opened); only
       pr_opened follow-up replies otherwise vanish into the Devin session.
    2. There's an unconsumed `followup_pending` marker — i.e. the most
       recent followup_pending has no later followup_replied event.
    3. `task.last_devin_update_at > pending.created_at` so the message is
       provably fresher than the user's question (rules out posting a
       pre-existing message that happened to look stable).
    4. The message is stable across polls: `prior_response_body ==
       latest_message`. First observation always fails this and waits one
       cycle; second observation of the same body confirms Devin has
       moved on. Without this we'd risk posting an interim "looking into
       it" note before the real answer settles.
    5. The message is substantive (>= 60 chars), matching the plan-mode
       length floor — keeps one-line progress chatter from tripping the post.
    """
    if task.status != TaskStatus.PR_OPENED.value:
        return
    if not latest_message or len(latest_message) < _FOLLOWUP_REPLY_MIN_CHARS:
        return
    if prior_response_body != latest_message:
        return
    if task.last_devin_update_at is None:
        return

    pending = (
        db.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == "followup_pending",
        )
        .order_by(InteractionEvent.id.desc())
        .first()
    )
    if pending is None:
        return

    replied = (
        db.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == "followup_replied",
        )
        .order_by(InteractionEvent.id.desc())
        .first()
    )
    if replied is not None and replied.id > pending.id:
        return

    if not _is_strictly_after(task.last_devin_update_at, pending.created_at):
        return

    body = "**Devin replied to the follow-up:**\n\n" + latest_message
    posted = _safe_post(
        gh,
        repo_full_name=task.repo_full_name,
        issue_number=task.issue_number,
        body=body,
    )
    if posted is None:
        # Transient GitHub failure — don't record consumption, retry next poll.
        return

    cid, url = _posted_meta(posted)
    _record_event(
        db,
        task_id=task.id,
        source="orchestrator",
        event_type="followup_replied",
        body=f"replied to followup_pending #{pending.id}",
        github_comment_id=cid,
        github_comment_url=url,
    )
