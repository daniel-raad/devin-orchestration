"""Client for the Devin API.

Adapter functions normalize the vendor response into a stable internal shape:
{session_id, session_url, status, pr_url, latest_message, error}

The exact Devin REST shape can drift; the adapters below try several common
keys and degrade gracefully.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx


def _first(d: dict, keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _adapt_session(data: dict) -> dict:
    # v3 API uses pull_requests array
    pr = None
    pull_requests = data.get("pull_requests")
    if isinstance(pull_requests, list) and pull_requests:
        pr = pull_requests[0].get("pr_url")

    # Fallback to v1/v2 format for backward compatibility
    if pr is None:
        pr = _first(data, ["pr_url", "pull_request_url"])
        if pr is None:
            pull = data.get("pull_request") or data.get("pr")
            if isinstance(pull, dict):
                pr = pull.get("url") or pull.get("html_url")
            elif isinstance(pull, str):
                pr = pull

    latest = _first(data, ["latest_message", "last_message"])
    if latest is None:
        msgs = data.get("messages")
        if isinstance(msgs, list) and msgs:
            last = msgs[-1]
            if isinstance(last, dict):
                latest = last.get("content") or last.get("text") or last.get("message")
            else:
                latest = str(last)

    raw_status = _first(data, ["status_enum", "status", "state"], "unknown")
    status_detail = _first(data, ["status_detail"])

    # v3 API quirk: top-level `status` stays "running" until the session
    # ends entirely. The actual "Devin's done with this turn, waiting for
    # me" signal is `status_detail = "waiting_for_user"`. Map it onto our
    # internal `awaiting_user` so the orchestrator's existing settled-state
    # detection (and plan-mode response_ready) just works.
    status = (raw_status or "").lower()
    detail = (status_detail or "").lower()
    if status == "running" and detail in {"waiting_for_user", "waiting_user", "needs_input"}:
        status = "awaiting_user"
    elif status == "running" and detail in {"completed", "finished", "succeeded"}:
        status = "completed"

    return {
        "session_id": _first(data, ["session_id", "id"]),
        "session_url": _first(data, ["session_url", "url", "html_url"]),
        "status": status,
        "status_detail": status_detail,
        "pr_url": pr,
        "latest_message": latest,
        "error": _first(data, ["error", "error_message"]),
    }


def _extract_latest_devin_message(messages_payload: Any) -> Optional[str]:
    """Pull the latest Devin-authored message body out of a /messages
    response. Returns None if not found."""
    items = (
        messages_payload.get("items")
        if isinstance(messages_payload, dict) and "items" in messages_payload
        else messages_payload
    )
    if not isinstance(items, list):
        return None
    devin_msgs = [
        m for m in items if isinstance(m, dict) and m.get("source") == "devin"
    ]
    if not devin_msgs:
        return None
    last = devin_msgs[-1]
    return last.get("message") or last.get("content") or last.get("text")


class DevinClient:
    def __init__(
        self,
        api_key: str,
        org_id: str = "",
        base_url: str = "https://api.devin.ai/v3",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.org_id = org_id
        self.base_url = base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def create_session(self, prompt: str, repo_full_name: Optional[str] = None) -> dict:
        if not self.org_id:
            raise ValueError("org_id is required for v3 API")
        
        body: dict[str, Any] = {"prompt": prompt}
        if repo_full_name:
            body["repos"] = [repo_full_name]
        
        resp = self._client.post(f"/organizations/{self.org_id}/sessions", json=body)
        resp.raise_for_status()
        return _adapt_session(resp.json())

    def send_message(self, session_id: str, message: str) -> dict:
        if not self.org_id:
            raise ValueError("org_id is required for v3 API")
        
        # v3 API requires devin_id prefix (e.g., devin-abc123)
        devin_id = session_id if session_id.startswith("devin-") else f"devin-{session_id}"
        
        resp = self._client.post(
            f"/organizations/{self.org_id}/sessions/{devin_id}/messages",
            json={"message": message},
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {}
        return {"ok": True, "data": data}

    def get_session(self, session_id: str) -> dict:
        if not self.org_id:
            raise ValueError("org_id is required for v3 API")

        # v3 API requires devin_id prefix (e.g., devin-abc123)
        devin_id = session_id if session_id.startswith("devin-") else f"devin-{session_id}"

        resp = self._client.get(f"/organizations/{self.org_id}/sessions/{devin_id}")
        resp.raise_for_status()
        adapted = _adapt_session(resp.json())

        # The v3 session endpoint doesn't include conversation messages.
        # If we don't already have a latest_message and the session is
        # settled (waiting for user / completed), pull the most recent
        # Devin-authored message from /messages so plan responses, PR
        # narratives, and clarification questions can be posted back on
        # the issue.
        if adapted.get("latest_message") is None:
            try:
                mresp = self._client.get(
                    f"/organizations/{self.org_id}/sessions/{devin_id}/messages"
                )
                if mresp.status_code == 200:
                    latest = _extract_latest_devin_message(mresp.json())
                    if latest:
                        adapted["latest_message"] = latest
            except Exception:
                pass
        return adapted

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
