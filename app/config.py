from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    devin_api_key: str = Field(default="")
    devin_org_id: str = Field(default="")
    devin_api_base: str = Field(default="https://api.devin.ai/v3")

    github_token: str = Field(default="")
    github_webhook_secret: str = Field(default="")
    github_api_base: str = Field(default="https://api.github.com")

    # Optional: authenticate as a GitHub App instead of with a PAT.
    # If app_id + private_key (or path) are set, App auth is used and
    # github_token is ignored for outgoing API calls.
    github_app_id: str = Field(default="")
    github_app_private_key: str = Field(default="")
    github_app_private_key_path: str = Field(default="")

    app_base_url: str = Field(default="http://localhost:8000")
    database_url: str = Field(default="sqlite:///./data/devin.db")

    target_repo: str = Field(default="")
    bot_github_login: str = Field(default="")

    poller_enabled: bool = Field(default=True)
    poller_interval_seconds: int = Field(default=45)
    poller_concurrency: int = Field(default=8)

    # Cap on new Devin sessions per (repo, rolling 60 minutes). Comments
    # over the limit get a polite refusal rather than a runaway ACU bill.
    rate_limit_sessions_per_hour: int = Field(default=30)
