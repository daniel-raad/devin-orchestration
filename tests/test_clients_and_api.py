"""Smoke tests for the client adapter shapes and remaining API endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json

from tests.conftest import make_issue_comment_payload


def test_devin_client_adapter_normalizes_response_shape(monkeypatch):
    """Adapter should map vendor response onto our internal shape."""
    from app.devin_client import DevinClient

    captured = {}

    class FakeResp:
        def __init__(self, json_data, status=200):
            self._json = json_data
            self.status_code = status

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class FakeHttpx:
        def __init__(self, *a, **kw):
            captured["init"] = kw

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResp(
                {
                    "session_id": "devin-abc-1",
                    "url": "https://app.devin.ai/sessions/devin-abc-1",
                    "status": "running",
                    "org_id": "org-123",
                    "pull_requests": [],
                }
            )

        def get(self, url, headers=None):
            return FakeResp(
                {
                    "session_id": "devin-abc-1",
                    "url": "https://app.devin.ai/sessions/devin-abc-1",
                    "status": "blocked",
                    "pull_requests": [{"pr_url": "https://github.com/o/r/pull/9"}],
                    "messages": [{"role": "assistant", "content": "hello"}],
                }
            )

        def close(self):
            pass

    monkeypatch.setattr("app.devin_client.httpx.Client", FakeHttpx)

    client = DevinClient(api_key="key", org_id="org-123", base_url="https://api.devin.ai/v3")

    created = client.create_session(prompt="do work", repo_full_name="o/r")
    assert created["session_id"] == "devin-abc-1"
    assert created["session_url"] == "https://app.devin.ai/sessions/devin-abc-1"
    assert created["status"] == "running"
    assert created["pr_url"] is None

    fetched = client.get_session("devin-abc-1")
    assert fetched["status"] == "blocked"
    assert fetched["pr_url"] == "https://github.com/o/r/pull/9"
    assert fetched["latest_message"] == "hello"


def test_devin_v3_waiting_for_user_status_detail_mapped(monkeypatch):
    """Devin's v3 keeps top-level `status='running'` until the session
    ends; the actual 'awaiting user input' signal is `status_detail =
    'waiting_for_user'`. The adapter must normalize that onto our
    internal `awaiting_user` so plan-mode response_ready() fires.
    Additionally, the v3 session endpoint doesn't include `messages`
    inline, so the client must follow up with /messages to extract the
    latest Devin-authored message body."""
    from app.devin_client import DevinClient

    class FakeResp:
        def __init__(self, body, status=200):
            self._body = body
            self.status_code = status

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class FakeHttpx:
        def __init__(self, *a, **kw):
            pass

        def post(self, *a, **kw):
            raise AssertionError("get_session must not POST")

        def get(self, url, headers=None):
            if url.endswith("/messages"):
                return FakeResp(
                    {
                        "items": [
                            {"source": "user", "message": "the prompt"},
                            {
                                "source": "devin",
                                "message": "## Plan\n\nDo X then Y.",
                            },
                        ]
                    }
                )
            return FakeResp(
                {
                    "session_id": "devin-xyz",
                    "url": "https://app.devin.ai/sessions/devin-xyz",
                    "status": "running",
                    "status_detail": "waiting_for_user",
                    "pull_requests": [],
                }
            )

        def close(self):
            pass

    monkeypatch.setattr("app.devin_client.httpx.Client", FakeHttpx)
    client = DevinClient(api_key="k", org_id="org-123")

    snap = client.get_session("devin-xyz")
    assert snap["status"] == "awaiting_user"
    assert snap["status_detail"] == "waiting_for_user"
    # Plan body pulled from /messages.
    assert snap["latest_message"] == "## Plan\n\nDo X then Y."


def test_github_client_signature_verification():
    from app.github_client import verify_signature

    secret = "shhh"
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(secret, body, sig) is True
    assert verify_signature(secret, body, "sha256=deadbeef") is False
    assert verify_signature(secret, body, None) is False
    # Empty secret means we don't enforce verification.
    assert verify_signature("", body, None) is True


def test_github_client_app_auth_flow(monkeypatch):
    """When app_id + private_key are set, the client should mint an installation
    access token before posting comments."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.github_client import GitHubClient

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    captured = []

    class FakeResp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class FakeHttpx:
        def __init__(self, *a, **kw):
            pass

        def get(self, url, headers=None):
            captured.append(("GET", url, dict(headers or {})))
            if url.endswith("/installation"):
                return FakeResp({"id": 99})
            return FakeResp({})

        def post(self, url, json=None, headers=None):
            captured.append(("POST", url, dict(headers or {}), json))
            if url.endswith("/access_tokens"):
                return FakeResp({
                    "token": "ghs_install_xyz",
                    "expires_at": "2099-01-01T00:00:00Z",
                })
            if "/issues/" in url and url.endswith("/comments"):
                return FakeResp({"id": 1, "html_url": "https://x"})
            return FakeResp({})

        def close(self):
            pass

    monkeypatch.setattr("app.github_client.httpx.Client", FakeHttpx)

    gh = GitHubClient(app_id="123456", private_key=pem)
    assert gh.is_app is True

    posted = gh.post_issue_comment("o/r", 1, "hello")
    assert posted == {"id": 1, "html_url": "https://x"}

    methods = [c[0] for c in captured]
    urls = [c[1] for c in captured]
    assert methods == ["GET", "POST", "POST"]
    assert urls[0] == "/repos/o/r/installation"
    assert urls[1] == "/app/installations/99/access_tokens"
    assert urls[2] == "/repos/o/r/issues/1/comments"

    # Comment POST must use the installation token, not the JWT.
    comment_auth = captured[2][2].get("Authorization")
    assert comment_auth == "Bearer ghs_install_xyz"

    # Second call should reuse the cached installation token (no new GET/POST
    # to /installation or /access_tokens).
    captured.clear()
    gh.post_issue_comment("o/r", 1, "again")
    assert [c[0] for c in captured] == ["POST"]
    assert captured[0][1] == "/repos/o/r/issues/1/comments"


def test_webhook_signature_enforced_when_secret_set(client):
    client.app.state.settings.github_webhook_secret = "shh"
    payload = make_issue_comment_payload()
    body = json.dumps(payload).encode()
    sig = "sha256=deadbeef"

    res = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 401


def test_webhook_signature_passes_when_valid(client):
    secret = "shh-valid"
    client.app.state.settings.github_webhook_secret = secret
    payload = make_issue_comment_payload()
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    res = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 200
    assert res.json()["action"] == "session_created"


def test_tasks_listing_and_detail(client, db_session):
    payload = make_issue_comment_payload()
    client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    res = client.get("/api/tasks")
    assert res.status_code == 200
    tasks = res.json()
    assert len(tasks) == 1
    task_id = tasks[0]["id"]

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["task"]["id"] == task_id
    assert isinstance(body["events"], list)
    assert any(e["event_type"] == "user_instruction" for e in body["events"])


def test_send_endpoint_forwards_to_devin(client, mock_devin):
    payload = make_issue_comment_payload()
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    task_id = res.json()["task_id"]
    mock_devin.send_message.reset_mock()

    out = client.post(
        f"/api/tasks/{task_id}/send",
        json={"message": "@devin add another regression test"},
    )
    assert out.status_code == 200
    mock_devin.send_message.assert_called_once()


def test_refresh_endpoint_polls_devin(client, mock_devin):
    payload = make_issue_comment_payload()
    res = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )
    task_id = res.json()["task_id"]
    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "completed",
        "pr_url": "https://github.com/test-org/test-repo/pull/9",
        "latest_message": "Opened PR.",
        "error": None,
    }

    out = client.post(f"/api/tasks/{task_id}/refresh")
    assert out.status_code == 200
    body = out.json()
    assert body["task"]["pr_url"].endswith("/pull/9")
    assert body["task"]["status"] in {"completed", "pr_opened"}


def test_dashboard_root_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Devin" in res.text
