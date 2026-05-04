from __future__ import annotations

from tests.conftest import make_issue_comment_payload


def test_ignores_non_issue_comment_events(client, mock_devin, mock_github):
    payload = {"zen": "Keep it logically awesome.", "hook_id": 1}
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "ping"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "ignored"
    assert body["reason"] == "unsupported_event"
    mock_devin.create_session.assert_not_called()
    mock_github.post_issue_comment.assert_not_called()


def test_ignores_comment_without_devin_mention(client, mock_devin, mock_github):
    payload = make_issue_comment_payload(body="thanks for the report, looking into it")
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "ignored"
    assert body["reason"] == "no_devin_mention"
    mock_devin.create_session.assert_not_called()


def test_ignores_pull_request_comments(client, mock_devin, mock_github):
    payload = make_issue_comment_payload(is_pull_request=True)
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "ignored"
    assert body["reason"] == "pull_request_comment"
    mock_devin.create_session.assert_not_called()


def test_ignores_bot_self_comments(client, mock_devin, mock_github):
    # Configure BOT_GITHUB_LOGIN; sender matches → ignored.
    client.app.state.settings.bot_github_login = "devin-bot"
    payload = make_issue_comment_payload(sender_login="devin-bot")
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "ignored"
    assert body["reason"] == "bot_comment"
    mock_devin.create_session.assert_not_called()


def test_ignores_any_bot_suffixed_sender_regardless_of_config(client, mock_devin):
    """Any sender whose login ends with [bot] is a GitHub App identity and
    must be filtered, even when BOT_GITHUB_LOGIN is unset. This is the
    primary loop protection in App-auth deployments — the orchestrator's
    own status comments can contain literal `@devin` text which would
    otherwise round-trip through the @devin mention filter.
    """
    # Default conftest config has BOT_GITHUB_LOGIN unset.
    payload = make_issue_comment_payload(
        sender_login="some-other-org-bot[bot]",
        body="@devin can you also look at this — do not implement, just plan",
    )
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert res.status_code == 200
    assert res.json()["reason"] == "bot_comment"
    mock_devin.create_session.assert_not_called()


def test_ignores_non_created_actions(client, mock_devin):
    payload = make_issue_comment_payload(action="edited")
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "ignored"
    assert body["reason"] == "non_created_action"
    mock_devin.create_session.assert_not_called()


def test_accepts_valid_issue_comment_with_devin_mention(client, mock_devin, mock_github):
    payload = make_issue_comment_payload()
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "session_created"
    mock_devin.create_session.assert_called_once()
    mock_github.post_issue_comment.assert_called()
