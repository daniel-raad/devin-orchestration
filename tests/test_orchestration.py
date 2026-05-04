from __future__ import annotations

from app.models import InteractionEvent, RemediationTask, TaskStatus
from tests.conftest import make_issue_comment_payload


def _post(client, payload):
    return client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )


def test_creates_devin_session_with_correct_issue_context(client, mock_devin):
    payload = make_issue_comment_payload(
        issue_title="[VULN] SSRF in image proxy",
        issue_body="Body describing the vulnerability.",
        body="@devin remediate",
    )
    _post(client, payload)

    mock_devin.create_session.assert_called_once()
    kwargs = mock_devin.create_session.call_args.kwargs or {}
    args = mock_devin.create_session.call_args.args
    prompt = kwargs.get("prompt") or (args[0] if args else "")
    repo = kwargs.get("repo_full_name") or (args[1] if len(args) > 1 else "")

    assert "SSRF in image proxy" in prompt
    assert "Body describing the vulnerability." in prompt
    assert "@devin remediate" in prompt
    assert repo == "test-org/test-repo"


def test_forwards_followup_to_existing_session(client, mock_devin):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    _post(client, make_issue_comment_payload(comment_id=2, body="@devin also test invalid filenames"))

    mock_devin.send_message.assert_called_once()
    kwargs = mock_devin.send_message.call_args.kwargs or {}
    args = mock_devin.send_message.call_args.args
    session_id = kwargs.get("session_id") or (args[0] if args else None)
    message = kwargs.get("message") or (args[1] if len(args) > 1 else None)

    assert session_id == "devin-session-1"
    assert "test invalid filenames" in message


def test_records_interaction_events(client, db_session):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    _post(client, make_issue_comment_payload(comment_id=2, body="@devin add a test"))

    events = db_session.query(InteractionEvent).order_by(InteractionEvent.id).all()
    types = [e.event_type for e in events]
    assert "user_instruction" in types
    assert "followup_forwarded" in types

    sources = {e.source for e in events}
    assert "github" in sources
    assert "orchestrator" in sources


def test_handles_devin_api_failures_cleanly(client, mock_devin, mock_github, db_session):
    mock_devin.create_session.side_effect = RuntimeError("Devin upstream 500")

    res = _post(client, make_issue_comment_payload())

    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "session_failed"

    task = db_session.query(RemediationTask).first()
    assert task is not None
    assert task.status == TaskStatus.FAILED.value
    assert task.error and "Devin upstream 500" in task.error

    mock_github.post_issue_comment.assert_called()
    posted_body = mock_github.post_issue_comment.call_args.kwargs.get("body") or ""
    assert "fail" in posted_body.lower() or "error" in posted_body.lower()


def test_terminal_completed_task_does_not_create_new_session_without_retry(
    client, mock_devin, db_session
):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    task = db_session.query(RemediationTask).first()
    task.status = TaskStatus.DONE.value
    task.pr_url = "https://github.com/test-org/test-repo/pull/2"
    db_session.commit()

    mock_devin.create_session.reset_mock()
    res = _post(client, make_issue_comment_payload(comment_id=2, body="@devin thanks"))
    assert res.json()["action"] == "previous_task_complete"
    mock_devin.create_session.assert_not_called()


def test_devin_completed_without_pr_marks_done_not_failed(
    client, mock_devin, mock_github, db_session, app
):
    """Devin reporting `completed` on a remediate task with no PR is a
    legitimate terminal outcome (no code change needed), NOT a failure.
    The task lands in `done`, the dashboard's "PRs merged" metric does
    NOT count it (because pr_url is null), and a "done without a PR"
    comment is posted to the issue.
    """
    from app.orchestrator import refresh_task_from_devin
    from app.models import InteractionEvent, RemediationTask

    _post(client, make_issue_comment_payload(body="@devin remediate this"))
    task = db_session.query(RemediationTask).first()
    mock_github.post_issue_comment.reset_mock()

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "completed",
        "pr_url": None,
        "latest_message": "I investigated and found the issue does not require a code change.",
        "error": None,
    }

    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    db_session.refresh(task)
    assert task.status == TaskStatus.DONE.value
    assert task.pr_url is None
    assert task.time_to_completion_seconds is not None

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert any("without opening a PR" in b for b in bodies)
    # Must NOT post a `failed` comment.
    assert not any("**failed**" in b for b in bodies)

    # Metric: "PRs merged" excludes done-without-PR.
    metrics = client.get("/api/metrics").json()
    assert metrics["completed_tasks"] == 0
    assert metrics["done_no_change"] == 1
    assert metrics["failed_tasks"] == 0


def test_done_no_change_status_comment_is_deduped(
    client, mock_devin, mock_github, db_session, app
):
    """Two poller cycles with the same completed-no-PR snapshot must post
    the `done_no_change` comment exactly once."""
    from app.orchestrator import refresh_task_from_devin
    from app.models import RemediationTask

    _post(client, make_issue_comment_payload(body="@devin remediate this"))
    task = db_session.query(RemediationTask).first()

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "completed",
        "pr_url": None,
        "latest_message": "no change needed",
        "error": None,
    }

    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    no_pr_posts = sum(1 for b in bodies if "without opening a PR" in b)
    assert no_pr_posts == 1


def test_terminal_completed_task_creates_new_session_on_retry(
    client, mock_devin, db_session
):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    task = db_session.query(RemediationTask).first()
    task.status = TaskStatus.DONE.value
    task.pr_url = "https://github.com/test-org/test-repo/pull/2"
    db_session.commit()

    mock_devin.create_session.reset_mock()
    mock_devin.create_session.return_value = {
        "session_id": "devin-session-2",
        "session_url": "https://app.devin.ai/sessions/devin-session-2",
        "status": "running",
        "pr_url": None,
        "latest_message": None,
        "error": None,
    }

    res = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin retry: please continue and fix the regression"),
    )
    assert res.json()["action"] == "session_created"
    mock_devin.create_session.assert_called_once()


# ---------------------------------------------------------------------------
# Follow-up reply on `pr_opened` — marker + settle gate
# ---------------------------------------------------------------------------


_FOLLOWUP_REPLY_BODY = (
    "PR #6 was closed by automation when PR #9 superseded it — the bot detected "
    "PR #9 covered the same files and chose the newer one as the active "
    "remediation. No deliberate human action; you don't need to do anything."
)


def _setup_pr_opened_with_followup(client, db_session, mock_github):
    """Create a task, advance to pr_opened, send a follow-up question, and
    reset the GitHub mock so subsequent assertions only see new posts."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    task = db_session.query(RemediationTask).first()
    task.status = TaskStatus.PR_OPENED.value
    task.pr_url = "https://github.com/test-org/test-repo/pull/9"
    db_session.commit()

    _post(
        client,
        make_issue_comment_payload(
            comment_id=2,
            body="@devin do you know why my PR #6 was closed?",
        ),
    )
    db_session.refresh(task)
    mock_github.post_issue_comment.reset_mock()
    return task


def test_followup_on_pr_opened_records_pending_marker(client, db_session, mock_github):
    task = _setup_pr_opened_with_followup(client, db_session, mock_github)

    pending = (
        db_session.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.event_type == "followup_pending",
        )
        .all()
    )
    assert len(pending) == 1


def test_followup_on_remediating_does_not_record_pending_marker(client, db_session):
    """Marker is scoped to pr_opened — other phases use existing posting paths."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    task = db_session.query(RemediationTask).first()
    # Stay in remediating; default status from session creation.
    assert task.status == TaskStatus.REMEDIATING.value

    _post(client, make_issue_comment_payload(comment_id=2, body="@devin also handle Y"))

    markers = (
        db_session.query(InteractionEvent)
        .filter(InteractionEvent.event_type == "followup_pending")
        .count()
    )
    assert markers == 0


def test_followup_reply_posts_after_stable_substantive_message(
    client, db_session, mock_devin, mock_github, app
):
    """Two polls with the same substantive latest_message → one reply post."""
    from app.orchestrator import refresh_task_from_devin

    task = _setup_pr_opened_with_followup(client, db_session, mock_github)

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",  # deliberately not a settled status
        "pr_url": task.pr_url,
        "latest_message": _FOLLOWUP_REPLY_BODY,
        "error": None,
    }

    # First poll: records devin_response, but stability check fails (no prior).
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    bodies_after_first = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert not any("Devin replied to the follow-up" in b for b in bodies_after_first)

    # Second poll: same message, stability check passes → posts.
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    bodies_after_second = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    reply_posts = [b for b in bodies_after_second if "Devin replied to the follow-up" in b]
    assert len(reply_posts) == 1
    assert _FOLLOWUP_REPLY_BODY in reply_posts[0]


def test_followup_reply_is_deduped_after_post(
    client, db_session, mock_devin, mock_github, app
):
    """A third poll after posting must not re-post the same reply."""
    from app.orchestrator import refresh_task_from_devin

    task = _setup_pr_opened_with_followup(client, db_session, mock_github)

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": task.pr_url,
        "latest_message": _FOLLOWUP_REPLY_BODY,
        "error": None,
    }

    for _ in range(3):
        refresh_task_from_devin(
            db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
        )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    reply_posts = [b for b in bodies if "Devin replied to the follow-up" in b]
    assert len(reply_posts) == 1


def test_followup_reply_does_not_post_short_message(
    client, db_session, mock_devin, mock_github, app
):
    """A message under the length floor must NOT be posted, even if stable."""
    from app.orchestrator import refresh_task_from_devin

    task = _setup_pr_opened_with_followup(client, db_session, mock_github)

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": task.pr_url,
        "latest_message": "looking into it",  # < 60 chars
        "error": None,
    }

    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert not any("Devin replied to the follow-up" in b for b in bodies)


def test_followup_reply_does_not_post_stale_message_predating_question(
    client, db_session, mock_devin, mock_github, app
):
    """If the stable message predates the user's follow-up, the freshness
    check fails and we don't post (rules out posting a pre-existing
    Devin message that just happens to be stable)."""
    from datetime import timedelta

    from app.orchestrator import _utcnow, refresh_task_from_devin

    task = _setup_pr_opened_with_followup(client, db_session, mock_github)

    # Force last_devin_update_at to BEFORE the followup_pending marker.
    pending = (
        db_session.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.event_type == "followup_pending",
        )
        .first()
    )
    task.last_devin_update_at = (
        pending.created_at - timedelta(minutes=5)
    ).replace(tzinfo=None) if pending.created_at.tzinfo is None else pending.created_at - timedelta(minutes=5)
    # Pre-seed a devin_response event so stability check would otherwise pass.
    db_session.add(
        InteractionEvent(
            task_id=task.id,
            source="devin",
            event_type="devin_response",
            body=_FOLLOWUP_REPLY_BODY,
        )
    )
    db_session.commit()

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": task.pr_url,
        "latest_message": _FOLLOWUP_REPLY_BODY,
        "error": None,
    }

    # Two polls — neither should post, because Devin hasn't sent anything
    # newer than the user's follow-up. (Note: the polls themselves don't
    # bump last_devin_update_at because the message body is unchanged.)
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert not any("Devin replied to the follow-up" in b for b in bodies)


def test_followup_reply_retries_after_transient_github_failure(
    client, db_session, mock_devin, mock_github, app
):
    """If the GitHub post fails (transient 5xx), the marker stays open and
    the next poll retries. No `followup_replied` event is recorded on
    failure, so the next stable poll re-attempts."""
    from app.orchestrator import refresh_task_from_devin

    task = _setup_pr_opened_with_followup(client, db_session, mock_github)

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": task.pr_url,
        "latest_message": _FOLLOWUP_REPLY_BODY,
        "error": None,
    }

    # Two polls to satisfy stability, but GitHub fails on every call.
    mock_github.post_issue_comment.side_effect = RuntimeError("github 504")
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    replied_count = (
        db_session.query(InteractionEvent)
        .filter(InteractionEvent.event_type == "followup_replied")
        .count()
    )
    assert replied_count == 0  # no consumption yet → retry path is open

    # GitHub recovers; third poll should succeed and record the consumption.
    mock_github.post_issue_comment.side_effect = None
    mock_github.post_issue_comment.return_value = {
        "id": 555001,
        "html_url": "https://github.com/test-org/test-repo/issues/1#issuecomment-555001",
    }
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    replied_after = (
        db_session.query(InteractionEvent)
        .filter(InteractionEvent.event_type == "followup_replied")
        .count()
    )
    assert replied_after == 1
