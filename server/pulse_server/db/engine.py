from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def _enable_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Apply SQLite pragmas on every new connection.

    WAL dramatically improves read/write concurrency (the single-writer stays, but readers
    no longer block it). `synchronous=NORMAL` is the recommended durability/performance
    trade-off for WAL. `foreign_keys=ON` is enforced because SQLite defaults to off.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.close()


def build_engine(db_url: str, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(db_url, echo=echo, future=True)
    _enable_sqlite_pragmas(engine)
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
