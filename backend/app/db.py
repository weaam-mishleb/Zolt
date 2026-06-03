"""SQLAlchemy engine + connection pool and the FastAPI DB dependency."""
from __future__ import annotations

import ssl
from collections.abc import Generator

from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


def _connect_args() -> dict:
    """PyMySQL connect args: generous WAN timeouts, plus a TLS context for managed
    hosts. Railway/cloud MySQL runs with SSL on and drops plaintext handshakes, so
    we enable TLS (no cert verification — we trust the proxy) for *.rlwy.net hosts
    or when DB_SSL is set. Local plaintext is untouched."""
    args: dict = {
        "connect_timeout": settings.db_connect_timeout,
        "read_timeout": settings.db_read_timeout,
        "write_timeout": settings.db_write_timeout,
    }
    host = (make_url(settings.database_url).host or "").lower()
    if settings.db_ssl or host.endswith("rlwy.net") or "railway" in host:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        args["ssl"] = ctx
    return args


# A single engine for the process. QueuePool keeps `db_pool_size` live
# connections, opening up to `db_max_overflow` extra under load.
# `pool_pre_ping` transparently replaces connections dropped by MySQL while idle;
# `pool_recycle` proactively retires stale ones; `connect_args` carries the WAN
# timeouts and the TLS context for managed cloud DBs.
engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,
    connect_args=_connect_args(),
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
