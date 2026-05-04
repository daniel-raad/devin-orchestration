from __future__ import annotations

from app.models import InteractionEvent, RemediationTask


def _payload(**overrides):
    base = {
        "repo_full_name": "your-org/superset",
        "issue_number": 1,
        "issue_title": "[VULN] Demo issue",
        "issue_url": "https://github.com/your-org/superset/issues/1",
        "issue_body": "Body",
        "comment_body": "@devin please remediate",
        "comment_author": "demo-user",
    }
    base.update(overrides)
    return base


def test_simulation_creates_task_and_events(client, db_session, mock_devin):
    res = client.post("/api/simulate-comment", json=_payload())
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "session_created"
    assert body["task_id"]

    tasks = db_session.query(RemediationTask).all()
    assert len(tasks) == 1
    assert tasks[0].repo_full_name == "your-org/superset"
    assert tasks[0].issue_number == 1
    assert tasks[0].devin_session_id == "devin-session-1"

    events = db_session.query(InteractionEvent).all()
    assert any(e.event_type == "user_instruction" for e in events)
    mock_devin.create_session.assert_called_once()


def test_simulation_followup_forwards_to_existing_session(client, mock_devin):
    client.post("/api/simulate-comment", json=_payload())
    res2 = client.post(
        "/api/simulate-comment",
        json=_payload(comment_body="@devin please add a regression test"),
    )
    assert res2.json()["action"] == "followup_forwarded"
    assert mock_devin.create_session.call_count == 1
    mock_devin.send_message.assert_called_once()


def test_simulation_payload_must_include_devin_mention(client, mock_devin):
    res = client.post(
        "/api/simulate-comment",
        json=_payload(comment_body="just a status update, no mention"),
    )
    assert res.status_code == 200
    assert res.json()["action"] == "ignored"
    mock_devin.create_session.assert_not_called()
