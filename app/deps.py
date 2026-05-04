from __future__ import annotations

from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Generator[Session, None, None]:
    db = request.app.state.db
    session = db.session()
    try:
        yield session
    finally:
        session.close()


def get_devin_client(request: Request):
    return request.app.state.devin_client


def get_github_client(request: Request):
    return request.app.state.github_client


def get_settings(request: Request):
    return request.app.state.settings
