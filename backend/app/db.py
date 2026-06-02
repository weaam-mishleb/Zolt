"""SQLAlchemy engine + connection pool and the FastAPI DB dependency."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

# A single engine for the process. QueuePool keeps `db_pool_size` live
# connections, opening up to `db_max_overflow` extra under load.
# `pool_pre_ping` transparently replaces connections dropped by MySQL while idle;
# `pool_recycle` proactively retires stale ones; `connect_timeout` tolerates a
# slow/flaky WAN to a managed cloud DB.
engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": settings.db_connect_timeout,
        "read_timeout": settings.db_read_timeout,
        "write_timeout": settings.db_write_timeout,
    },
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
)


def get_db() -> Generator[Session, None, None]:
    """Yield a session per request; always closed (returned to the pool)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
