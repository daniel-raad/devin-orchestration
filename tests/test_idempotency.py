from __future__ import annotations

from tests.conftest import make_issue_comment_payload


def _post(client, payload):
    return client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )


def test_first_devin_comment_creates_session(client, mock_devin):
    res = _post(client, make_issue_comment_payload(comment_id=1))
    assert res.status_code == 200
    assert res.json()["action"] == "session_created"
    assert mock_devin.create_session.call_count == 1


def test_followup_comment_reuses_existing_session(client, mock_devin):
    first = _post(client, make_issue_comment_payload(comment_id=1, body="@devin please remediate"))
    assert first.json()["action"] == "session_created"

    second = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin also add a regression test"),
    )
    body = second.json()
    assert body["action"] == "followup_forwarded"
    assert mock_devin.create_session.call_count == 1
    mock_devin.send_message.assert_called_once()


def test_duplicate_comment_does_not_create_duplicate_session(client, mock_devin):
    payload = make_issue_comment_payload(comment_id=42, body="@devin go")
    first = _post(client, payload)
    assert first.json()["action"] == "session_created"

    duplicate = _post(client, payload)
    assert duplicate.json()["action"] == "duplicate_ignored"
    assert mock_devin.create_session.call_count == 1
    mock_devin.send_message.assert_not_called()


def test_pre_existing_claim_blocks_processing_without_devin_call(
    client, db_session, mock_devin
):
    """Simulates a cross-process race: another worker has already
    claimed this github_comment_id (inserted a ProcessedComment row)
    but we, in this process, haven't seen the InteractionEvent yet.
    The orchestrator must short-circuit on IntegrityError without
    creating a task and without calling Devin.
    """
    from app.models import ProcessedComment, RemediationTask

    db_session.add(ProcessedComment(github_comment_id=77777))
    db_session.commit()

    res = _post(
        client,
        make_issue_comment_payload(comment_id=77777, body="@devin remediate"),
    )
    assert res.json()["action"] == "duplicate_ignored"
    assert res.json()["reason"] == "duplicate_comment_id"
    assert db_session.query(RemediationTask).count() == 0
    mock_devin.create_session.assert_not_called()


def test_multiple_issues_get_separate_sessions(client, mock_devin):
    mock_devin.create_session.side_effect = [
        {
            "session_id": "s-1",
            "session_url": "https://app.devin.ai/sessions/s-1",
            "status": "running",
            "pr_url": None,
            "latest_message": None,
            "error": None,
        },
        {
            "session_id": "s-2",
            "session_url": "https://app.devin.ai/sessions/s-2",
            "status": "running",
            "pr_url": None,
            "latest_message": None,
            "error": None,
        },
    ]
    a = _post(client, make_issue_comment_payload(issue_number=1, comment_id=1))
    b = _post(client, make_issue_comment_payload(issue_number=2, comment_id=2))
    assert a.json()["action"] == "session_created"
    assert b.json()["action"] == "session_created"
    assert mock_devin.create_session.call_count == 2
