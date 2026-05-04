from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        connect_args: dict = {}
        engine_kwargs: dict = {}
        is_sqlite = url.startswith("sqlite")
        is_in_memory = is_sqlite and (":memory:" in url or url.endswith(":memory:"))
        if is_sqlite:
            connect_args["check_same_thread"] = False
            if is_in_memory:
                engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, connect_args=connect_args, **engine_kwargs)

        # File-backed SQLite: turn on WAL journaling (concurrent reads while a
        # write is in flight) and a 5s busy timeout so concurrent writers
        # transparently wait for the lock instead of failing immediately.
        # In-memory SQLite gets neither (no on-disk journal, single connection).
        if is_sqlite and not is_in_memory:
            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                finally:
                    cursor.close()

        self.SessionLocal = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def create_all(self) -> None:
        # Importing here ensures models are registered before metadata.create_all.
        from app import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.SessionLocal()
