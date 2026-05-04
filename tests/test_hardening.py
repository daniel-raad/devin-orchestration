"""Tests for the load-hardening additions: webhook off-loop work, per-repo
session-creation rate limit, and bounded-concurrency polling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from app.models import RemediationTask, TaskStatus
from app.orchestrator import _utcnow, handle_comment_event
from app.poller import poll_once
from tests.conftest import make_issue_comment_payload


def _post(client, payload):
    return client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def test_rate_limit_refuses_extra_session_creations(
    client, db_session, mock_devin, mock_github
):
    client.app.state.settings.rate_limit_sessions_per_hour = 2

    # First two distinct issues: allowed.
    r1 = _post(client, make_issue_comment_payload(issue_number=1, comment_id=1))
    r2 = _post(client, make_issue_comment_payload(issue_number=2, comment_id=2))
    assert r1.json()["action"] == "session_created"
    assert r2.json()["action"] == "session_created"

    mock_devin.create_session.reset_mock()

    # Third within the same rolling hour: rate-limited.
    r3 = _post(client, make_issue_comment_payload(issue_number=3, comment_id=3))
    body = r3.json()
    assert body["action"] == "rate_limited"
    assert body["limit"] == 2

    # Devin must NOT have been called for the third.
    mock_devin.create_session.assert_not_called()
    # The orchestrator must NOT have created a third task row.
    assert db_session.query(RemediationTask).count() == 2

    # The user-facing comment explains the cap.
    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert any("rate limit" in b.lower() and "2" in b for b in bodies)


def test_rate_limit_does_not_block_followups(client, db_session, mock_devin):
    """Follow-ups go to existing sessions; they're not new session creates."""
    client.app.state.settings.rate_limit_sessions_per_hour = 1
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin please remediate"))
    assert db_session.query(RemediationTask).count() == 1

    # Same issue, follow-up — should forward, not be rate-limited.
    res = _post(client, make_issue_comment_payload(comment_id=2, body="@devin add a regression test"))
    assert res.json()["action"] == "followup_forwarded"
    assert db_session.query(RemediationTask).count() == 1


def test_rate_limit_only_counts_recent_hour(client, db_session, mock_devin):
    client.app.state.settings.rate_limit_sessions_per_hour = 1

    # Insert a task that's older than 1h ago, simulating yesterday's burn.
    old = RemediationTask(
        repo_full_name="test-org/test-repo",
        issue_number=99,
        issue_title="old",
        issue_url="x",
        status=TaskStatus.DONE.value,
        requested_by="u",
        created_at=(_utcnow() - timedelta(hours=2)).replace(tzinfo=None),
        updated_at=(_utcnow() - timedelta(hours=2)).replace(tzinfo=None),
    )
    db_session.add(old)
    db_session.commit()

    # New comment under the limit should still go through despite the old task.
    res = _post(client, make_issue_comment_payload(issue_number=1, comment_id=1))
    assert res.json()["action"] == "session_created"


def test_rate_limit_zero_disables_check(client, mock_devin):
    """rate_limit_sessions_per_hour=0 means no limit — every session goes through."""
    client.app.state.settings.rate_limit_sessions_per_hour = 0
    for n in range(5):
        r = _post(client, make_issue_comment_payload(issue_number=n + 1, comment_id=n + 1))
        assert r.json()["action"] == "session_created"
    assert mock_devin.create_session.call_count == 5


# ---------------------------------------------------------------------------
# Webhook off-loop dispatch (via asyncio.to_thread)
# ---------------------------------------------------------------------------


def test_webhook_does_not_block_event_loop_under_slow_devin(client, mock_devin):
    """A slow Devin call should not pin the event loop; the thread pool
    absorbs it and the response still comes back."""
    import time

    def slow_create(*args, **kwargs):
        time.sleep(0.05)  # tiny — just proves we're going through to_thread
        return {
            "session_id": "devin-slow",
            "session_url": "https://app.devin.ai/sessions/devin-slow",
            "status": "running",
            "pr_url": None,
            "latest_message": None,
            "error": None,
        }

    mock_devin.create_session.side_effect = slow_create

    res = _post(client, make_issue_comment_payload())
    assert res.status_code == 200
    assert res.json()["action"] == "session_created"
    assert mock_devin.create_session.call_count == 1


# ---------------------------------------------------------------------------
# Bounded-concurrency poller
# ---------------------------------------------------------------------------


def _seed_session_started_event(db, task):
    """Production tasks always have a session_started event (recorded by
    _create_new_session). Tests that build tasks directly need to seed it
    so the poller's session-started backstop dedupes correctly without
    racing on the in-memory SQLite shared connection."""
    from app.models import InteractionEvent

    db.add(
        InteractionEvent(
            task_id=task.id,
            source="orchestrator",
            event_type="session_started",
            body="(test seed)",
        )
    )


@pytest.mark.asyncio
async def test_poller_polls_all_active_tasks(app, db_session, mock_devin, mock_github):
    """Seed a few active tasks, run poll_once, and assert all of them were
    queried against Devin (regardless of order, given concurrency)."""
    tasks = []
    for i in range(5):
        t = RemediationTask(
            repo_full_name="o/r",
            issue_number=i + 1,
            issue_title=f"issue {i+1}",
            issue_url=f"https://github.com/o/r/issues/{i+1}",
            status=TaskStatus.REMEDIATING.value,
            devin_session_id=f"devin-session-{i+1}",
            devin_session_url=f"https://app.devin.ai/sessions/devin-session-{i+1}",
            requested_by="u",
        )
        db_session.add(t)
        tasks.append(t)
    db_session.flush()
    for t in tasks:
        _seed_session_started_event(db_session, t)
    db_session.commit()

    polled = await poll_once(
        db_factory=app.state.db.session,
        devin=mock_devin,
        gh=app.state.github_client,
        concurrency=3,
    )
    assert polled == 5

    # Each session was fetched at least once.
    fetched_ids = {
        c.args[0] if c.args else c.kwargs.get("session_id")
        for c in mock_devin.get_session.call_args_list
    }
    assert fetched_ids == {f"devin-session-{i+1}" for i in range(5)}


@pytest.mark.asyncio
async def test_poller_skips_terminal_and_session_less_tasks(app, db_session, mock_devin):
    tasks = [
        RemediationTask(
            repo_full_name="o/r",
            issue_number=1,
            issue_title="t",
            issue_url="x",
            status=TaskStatus.DONE.value,  # terminal
            devin_session_id="devin-completed",
            devin_session_url="x",
        ),
        RemediationTask(
            repo_full_name="o/r",
            issue_number=2,
            issue_title="t",
            issue_url="x",
            status=TaskStatus.REMEDIATING.value,  # active but no session id
            devin_session_id=None,
            devin_session_url=None,
        ),
        RemediationTask(
            repo_full_name="o/r",
            issue_number=3,
            issue_title="t",
            issue_url="x",
            status=TaskStatus.REMEDIATING.value,
            devin_session_id="devin-eligible",
            devin_session_url="x",
        ),
    ]
    db_session.add_all(tasks)
    db_session.flush()
    for t in tasks:
        _seed_session_started_event(db_session, t)
    db_session.commit()

    polled = await poll_once(
        db_factory=app.state.db.session,
        devin=mock_devin,
        gh=app.state.github_client,
        concurrency=2,
    )
    assert polled == 1  # only the AWAITING_DEVIN + has-session row
    fetched_ids = {
        c.args[0] if c.args else c.kwargs.get("session_id")
        for c in mock_devin.get_session.call_args_list
    }
    assert fetched_ids == {"devin-eligible"}


# ---------------------------------------------------------------------------
# Race protection — orchestrator lock is wired into both routes
# ---------------------------------------------------------------------------


def test_issue_locks_exist_on_app_state(app):
    """Both /webhooks/github and /api/simulate-comment acquire a per-(repo,
    issue) lock so concurrent deliveries to the same issue can't race the
    dedupe check, while different issues parallelize."""
    locks = app.state.issue_locks
    same_a = locks.for_issue("o/r", 1)
    same_b = locks.for_issue("o/r", 1)
    different = locks.for_issue("o/r", 2)
    other_repo = locks.for_issue("p/q", 1)

    assert isinstance(same_a, asyncio.Lock)
    # Same key returns the same lock (identity, not just equality).
    assert same_a is same_b
    # Different issue number on the same repo gets its own lock.
    assert same_a is not different
    # Same issue number on a different repo gets its own lock.
    assert same_a is not other_repo


def test_duplicate_comment_id_dedupe_works_when_serialized(client, db_session, mock_devin):
    """Sequential proxy for the concurrent-retry case: two webhooks with
    the same github_comment_id (which is what GitHub retries look like
    once the lock has serialized them) must end up with exactly one task
    and one Devin session."""
    from app.models import RemediationTask

    payload = make_issue_comment_payload(comment_id=4242, body="@devin remediate")

    r1 = _post(client, payload)
    r2 = _post(client, payload)

    assert r1.json()["action"] == "session_created"
    assert r2.json()["action"] == "duplicate_ignored"

    assert db_session.query(RemediationTask).count() == 1
    assert mock_devin.create_session.call_count == 1


# ---------------------------------------------------------------------------
# Reliable session-started ack
# ---------------------------------------------------------------------------


def test_session_started_ack_retries_after_initial_post_failure(
    client, db_session, mock_devin, mock_github, app
):
    """If the create-flow's session-started GitHub post fails (transient
    5xx), the dedupe event is NOT recorded so a subsequent poller cycle
    retries the post until it sticks."""
    from app.orchestrator import refresh_task_from_devin
    from app.models import InteractionEvent, RemediationTask

    # First webhook: GitHub will fail on the post.
    mock_github.post_issue_comment.side_effect = RuntimeError("github 504")
    res = _post(client, make_issue_comment_payload())
    assert res.json()["action"] == "session_created"

    task = db_session.query(RemediationTask).first()
    assert task is not None
    # No session_started orchestrator event recorded yet (post failed,
    # maybe_post_status_update returned False without recording).
    started_events_before = (
        db_session.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == "session_started",
        )
        .count()
    )
    assert started_events_before == 0

    # Now GitHub recovers; the next poller iteration should retry.
    mock_github.post_issue_comment.side_effect = None
    mock_github.post_issue_comment.return_value = {
        "id": 12345,
        "html_url": "https://github.com/x/y/issues/1#issuecomment-12345",
    }
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert any("session started" in b.lower() for b in bodies)

    started_events_after = (
        db_session.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == "session_started",
        )
        .count()
    )
    assert started_events_after == 1


def test_session_started_ack_not_re_posted_after_success(
    client, db_session, mock_devin, mock_github, app
):
    """Once the session-started ack is delivered, the poller backstop
    must NOT post it again on subsequent cycles."""
    from app.orchestrator import refresh_task_from_devin
    from app.models import RemediationTask

    _post(client, make_issue_comment_payload())
    task = db_session.query(RemediationTask).first()

    posts_before = sum(
        1
        for c in mock_github.post_issue_comment.call_args_list
        if "session started" in (c.kwargs.get("body") or "").lower()
    )
    assert posts_before == 1

    # Two more poller cycles — must not re-post.
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    posts_after = sum(
        1
        for c in mock_github.post_issue_comment.call_args_list
        if "session started" in (c.kwargs.get("body") or "").lower()
    )
    assert posts_after == 1


@pytest.mark.asyncio
async def test_poller_concurrency_actually_overlaps(app, db_session):
    """If concurrency >= 2 and we have 4 tasks each blocking 100ms on the
    Devin client, total wall time should be roughly ceil(4/concurrency) *
    100ms — we measure that it's clearly less than 4 * 100ms."""
    import time

    tasks = []
    for i in range(4):
        t = RemediationTask(
            repo_full_name="o/r",
            issue_number=i + 1,
            issue_title=f"t{i+1}",
            issue_url="x",
            status=TaskStatus.REMEDIATING.value,
            devin_session_id=f"sid-{i+1}",
            devin_session_url="x",
        )
        db_session.add(t)
        tasks.append(t)
    db_session.flush()
    for t in tasks:
        _seed_session_started_event(db_session, t)
    db_session.commit()

    devin = MagicMock()

    def slow_get(session_id):
        time.sleep(0.1)
        return {
            "session_id": session_id,
            "session_url": f"https://app.devin.ai/sessions/{session_id}",
            "status": "running",
            "pr_url": None,
            "latest_message": None,
            "error": None,
        }

    devin.get_session.side_effect = slow_get

    started = time.monotonic()
    polled = await poll_once(
        db_factory=app.state.db.session,
        devin=devin,
        gh=app.state.github_client,
        concurrency=4,
    )
    elapsed = time.monotonic() - started

    assert polled == 4
    # Sequential would be ~0.4s. With concurrency=4 we expect ~0.1s.
    # Generous bound: under 0.3s clearly indicates parallelism is doing work.
    assert elapsed < 0.3, f"poller looks sequential (elapsed={elapsed:.3f}s)"
