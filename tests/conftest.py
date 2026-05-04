from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# Ensure required env vars exist before app import.
os.environ.setdefault("DEVIN_API_KEY", "test-devin-key")
os.environ.setdefault("DEVIN_ORG_ID", "test-devin-org")
os.environ.setdefault("GITHUB_TOKEN", "test-gh-token")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8000")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BOT_GITHUB_LOGIN", "devin-bot")
os.environ.setdefault("POLLER_ENABLED", "false")


@pytest.fixture
def mock_devin() -> MagicMock:
    client = MagicMock()
    client.create_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": None,
        "latest_message": None,
        "error": None,
    }
    client.send_message.return_value = {"ok": True}
    client.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": None,
        "latest_message": None,
        "error": None,
    }
    return client


@pytest.fixture
def mock_github() -> MagicMock:
    client = MagicMock()
    client.post_issue_comment.return_value = {
        "id": 999001,
        "html_url": "https://github.com/test-org/test-repo/issues/1#issuecomment-999001",
    }
    client.get_issue.return_value = {"number": 1, "title": "x"}
    client.verify_signature.return_value = True
    return client


@pytest.fixture
def app_factory(mock_devin: MagicMock, mock_github: MagicMock):
    from app.main import create_app

    def _factory(**overrides: Any):
        return create_app(
            devin_client=overrides.get("devin", mock_devin),
            github_client=overrides.get("github", mock_github),
        )

    return _factory


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def db_session(app):
    """A db session bound to the same in-memory db as the running app."""
    db = app.state.db
    session = db.session()
    try:
        yield session
    finally:
        session.close()


def make_issue_comment_payload(
    *,
    body: str = "@devin please remediate this",
    sender_login: str = "test-user",
    issue_number: int = 1,
    issue_title: str = "Sample issue",
    issue_body: str = "Issue body content",
    repo: str = "test-org/test-repo",
    is_pull_request: bool = False,
    action: str = "created",
    comment_id: int = 100001,
) -> dict:
    issue: dict[str, Any] = {
        "number": issue_number,
        "title": issue_title,
        "body": issue_body,
        "html_url": f"https://github.com/{repo}/issues/{issue_number}",
        "user": {"login": "issue-author"},
    }
    if is_pull_request:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/{repo}/pulls/{issue_number}"}

    return {
        "action": action,
        "issue": issue,
        "comment": {
            "id": comment_id,
            "body": body,
            "user": {"login": sender_login},
            "html_url": f"https://github.com/{repo}/issues/{issue_number}#issuecomment-{comment_id}",
        },
        "repository": {"full_name": repo},
        "sender": {"login": sender_login},
    }
