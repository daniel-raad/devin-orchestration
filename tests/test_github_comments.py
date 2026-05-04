from __future__ import annotations

from tests.conftest import make_issue_comment_payload


def _post(client, payload):
    return client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )


def _bodies(mock_github) -> list[str]:
    return [
        (call.kwargs.get("body") or (call.args[2] if len(call.args) > 2 else ""))
        for call in mock_github.post_issue_comment.call_args_list
    ]


def test_posts_session_started_comment_after_creation(client, mock_github):
    _post(client, make_issue_comment_payload())
    bodies = _bodies(mock_github)
    assert any("Devin remediation session started" in b for b in bodies)
    assert any("https://app.devin.ai/sessions/devin-session-1" in b for b in bodies)


def test_posts_followup_forwarded_comment(client, mock_github):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    mock_github.post_issue_comment.reset_mock()
    _post(client, make_issue_comment_payload(comment_id=2, body="@devin add a test"))

    bodies = _bodies(mock_github)
    assert any("Forwarded" in b for b in bodies)
    assert any("https://app.devin.ai/sessions/devin-session-1" in b for b in bodies)


def test_posts_failure_comment_on_devin_error(client, mock_devin, mock_github):
    mock_devin.create_session.side_effect = RuntimeError("upstream error")
    _post(client, make_issue_comment_payload())
    bodies = _bodies(mock_github)
    assert any("fail" in b.lower() or "error" in b.lower() for b in bodies)


def test_post_failure_does_not_dedupe_so_next_call_retries(client, mock_github, db_session):
    """If GitHub returns an error on the status post, we MUST NOT record the
    dedupe event — otherwise the next poller iteration silently skips
    posting the most important state-change comment for that task."""
    from app.models import InteractionEvent, RemediationTask
    from app.orchestrator import maybe_post_status_update

    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    task = db_session.query(RemediationTask).first()
    assert task is not None

    mock_github.post_issue_comment.reset_mock()
    mock_github.post_issue_comment.side_effect = RuntimeError("github 503")

    posted = maybe_post_status_update(
        db=db_session,
        gh=client.app.state.github_client,
        task=task,
        status_kind="pr_opened",
        body="PR opened by Devin: https://example.com/pr/1",
    )
    assert posted is False  # post failed
    # No dedupe event recorded — retry must be possible.
    assert (
        db_session.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == "pr_opened",
        )
        .count()
        == 0
    )

    # Now GitHub recovers; the next attempt should actually post and
    # record the dedupe event.
    mock_github.post_issue_comment.side_effect = None
    mock_github.post_issue_comment.return_value = {
        "id": 999777,
        "html_url": "https://github.com/x/y/issues/1#issuecomment-999777",
    }
    posted_again = maybe_post_status_update(
        db=db_session,
        gh=client.app.state.github_client,
        task=task,
        status_kind="pr_opened",
        body="PR opened by Devin: https://example.com/pr/1",
    )
    assert posted_again is True
    assert (
        db_session.query(InteractionEvent)
        .filter(
            InteractionEvent.task_id == task.id,
            InteractionEvent.source == "orchestrator",
            InteractionEvent.event_type == "pr_opened",
        )
        .count()
        == 1
    )


def test_does_not_post_duplicate_comments_for_same_status(client, mock_github, db_session):
    """Same status update should not re-post if last_posted matches."""
    from app.models import RemediationTask

    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate"))
    task = db_session.query(RemediationTask).first()
    assert task is not None

    # Simulate a poll that emits the same "session_started" status
    # The orchestrator's poll handler must dedupe.
    from app.orchestrator import maybe_post_status_update

    mock_github.post_issue_comment.reset_mock()
    # This update is the same status that's already been posted, so no comment should fire.
    maybe_post_status_update(
        db=db_session,
        gh=client.app.state.github_client,
        task=task,
        status_kind="session_started",
        body="Devin remediation session started: ...",
    )
    maybe_post_status_update(
        db=db_session,
        gh=client.app.state.github_client,
        task=task,
        status_kind="session_started",
        body="Devin remediation session started: ...",
    )
    assert mock_github.post_issue_comment.call_count == 0
