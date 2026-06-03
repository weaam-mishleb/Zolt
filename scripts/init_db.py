"""Create the Zolt tables in the database the current settings point at.

Resilient to flaky / high-latency managed DBs (e.g. a distant Railway proxy that
drops the first connection): a dedicated engine with ``pool_pre_ping=True`` and a
long ``connect_timeout``, and the whole schema-apply is retried with exponential
backoff on transient drops (MySQL 2013 / 2006 / 1053). ``CREATE TABLE IF NOT
EXISTS`` makes re-running safe.

It skips ``CREATE DATABASE`` / ``USE`` / ``SET NAMES`` so it targets whatever
database the URL points to.

    DATABASE_URL='mysql://user:pass@host:port/db' python -m scripts.init_db
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from backend.app.config import PROJECT_ROOT, settings

SCHEMA = PROJECT_ROOT / "db" / "init" / "01_schema.sql"
_SKIP_PREFIXES = ("create database", "use ", "set names")
_TRANSIENT_CODES = {2013, 2006, 1053}  # lost connection / gone away / shutdown

# A dedicated, resilient engine (independent of the app's shared one) so a flaky
# WAN to the cloud DB can't sink the very first schema connection.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"connect_timeout": 60, "read_timeout": 120, "write_timeout": 120},
    future=True,
)


def _statements(sql: str) -> list[str]:
    out: list[str] = []
    for chunk in sql.split(";"):
        body = "\n".join(
            ln for ln in chunk.splitlines() if not ln.strip().startswith("--")
        ).strip()
        if body and not body.lower().startswith(_SKIP_PREFIXES):
            out.append(body)
    return out


def _is_transient(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None)
    return bool(args) and args[0] in _TRANSIENT_CODES


def _apply(statements: list[str]) -> None:
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def main(max_retries: int = 8) -> None:
    statements = _statements(SCHEMA.read_text(encoding="utf-8"))
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            _apply(statements)
            print(f"✅ applied {len(statements)} statements to "
                  f"{engine.url.host}/{engine.url.database}")
            return
        except OperationalError as exc:
            if attempt >= max_retries or not _is_transient(exc):
                raise
            code = exc.orig.args[0] if getattr(exc, "orig", None) else "?"
            print(f"  ! transient DB error {code} during init_db — retry "
                  f"{attempt + 1}/{max_retries} in {delay:.0f}s", file=sys.stderr)
            engine.dispose()  # force a brand-new connection on the next attempt
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


if __name__ == "__main__":
    main()
