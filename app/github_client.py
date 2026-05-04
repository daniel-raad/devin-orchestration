"""GitHub REST client.

Supports two authentication modes:

- **PAT** (default): set `token` and we use `Authorization: Bearer <token>`.
- **GitHub App**: set `app_id` + `private_key` and we mint installation
  access tokens per repo on-demand (cached in-memory until expiry).

App auth is preferred for production: comments are authored by the App's
own bot identity (`<app-slug>[bot]`) rather than impersonating a human.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)


def verify_signature(secret: str, body: bytes, signature_header: Optional[str]) -> bool:
    """Verify GitHub's X-Hub-Signature-256 header.

    If `secret` is empty, signature verification is skipped (returns True).
    """
    if not secret:
        return True
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


class GitHubClient:
    def __init__(
        self,
        token: str = "",
        app_id: str = "",
        private_key: str = "",
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.app_id = (app_id or "").strip()
        self.private_key = private_key or ""
        self.base_url = base_url.rstrip("/")
        self._installation_tokens: dict[str, tuple[str, float]] = {}
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )

    @property
    def is_app(self) -> bool:
        return bool(self.app_id and self.private_key)

    def _generate_jwt(self) -> str:
        # Lazy import so PAT-only deployments don't require PyJWT/cryptography
        # to be importable at construction time.
        import jwt  # type: ignore

        now = int(time.time())
        payload = {
            "iat": now - 60,           # backdate for clock skew
            "exp": now + 9 * 60,       # max 10 minutes per GitHub docs
            "iss": str(self.app_id),
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def _installation_token_for(self, repo_full_name: str) -> str:
        cached = self._installation_tokens.get(repo_full_name)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        jwt_token = self._generate_jwt()

        # Look up the installation that has access to this repo.
        r = self._client.get(
            f"/repos/{repo_full_name}/installation",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        if r.status_code == 404:
            raise RuntimeError(
                f"GitHub App is not installed on {repo_full_name}. "
                "Install the App on the repo and retry."
            )
        r.raise_for_status()
        installation_id = r.json()["id"]

        # Exchange JWT for an installation access token (~1h lifetime).
        r = self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        r.raise_for_status()
        data = r.json()
        token = data["token"]
        expires_at = data.get("expires_at", "")
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            exp = time.time() + 30 * 60

        self._installation_tokens[repo_full_name] = (token, exp)
        return token

    def _auth_headers(self, repo_full_name: str) -> dict[str, str]:
        if self.is_app:
            tok = self._installation_token_for(repo_full_name)
        elif self.token:
            tok = self.token
        else:
            return {}
        return {"Authorization": f"Bearer {tok}"}

    def post_issue_comment(
        self, repo_full_name: str, issue_number: int, body: str
    ) -> dict:
        resp = self._client.post(
            f"/repos/{repo_full_name}/issues/{issue_number}/comments",
            json={"body": body},
            headers=self._auth_headers(repo_full_name),
        )
        resp.raise_for_status()
        return resp.json()

    def get_issue(self, repo_full_name: str, issue_number: int) -> dict:
        resp = self._client.get(
            f"/repos/{repo_full_name}/issues/{issue_number}",
            headers=self._auth_headers(repo_full_name),
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
