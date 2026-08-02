"""Create the Zolt tables in the database the current settings point at.

Resilient to flaky / high-latency managed DBs (e.g. a distant Railway proxy that
drops the first connection): it uses the app's shared engine (``pool_pre_ping``,
long WAN timeouts, and a TLS context for cloud hosts), and the whole schema-apply
is retried with exponential backoff on transient drops (MySQL 2013 / 2006 / 1053).
``CREATE TABLE IF NOT EXISTS`` makes re-running safe.

It skips ``CREATE DATABASE`` / ``USE`` / ``SET NAMES`` so it targets whatever
database the URL points to.

    DATABASE_URL='mysql://user:pass@host:port/db' python -m scripts.init_db
"""
from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError  # base of OperationalError/InterfaceError/etc.

from backend.app.config import PROJECT_ROOT
from backend.app.db import engine  # shared engine: WAN timeouts + TLS for cloud DBs

SCHEMA_DIR = PROJECT_ROOT / "db" / "init"
_SKIP_PREFIXES = ("create database", "use ", "set names")
_TRANSIENT_CODES = {2013, 2006, 1053}  # lost connection / gone away / shutdown


def _split_on_semicolons(code: str) -> list[str]:
    """Split on ';' while respecting single-quoted strings.

    A naive `code.split(';')` cuts statements in half whenever a semicolon
    appears inside a string literal — and this schema uses `COMMENT '...'`
    heavily, e.g. COMMENT '1.000 = certain; low scores go to review'.
    """
    out: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    while i < len(code):
        ch = code[i]
        if in_string:
            if ch == "\\" and i + 1 < len(code):     # escaped char
                buf.append(ch)
                buf.append(code[i + 1])
                i += 2
                continue
            if ch == "'":
                if i + 1 < len(code) and code[i + 1] == "'":   # '' → literal quote
                    buf.append("''")
                    i += 2
                    continue
                in_string = False
            buf.append(ch)
        elif ch == "'":
            in_string = True
            buf.append(ch)
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def _statements(sql: str) -> list[str]:
    """Split a .sql file into executable statements.

    Line comments are stripped BEFORE splitting (a ';' inside a `--` comment
    would cut a statement in half), and the split itself is string-aware.
    """
    code = "\n".join(
        ln for ln in sql.splitlines() if not ln.strip().startswith("--")
    )
    out: list[str] = []
    for chunk in _split_on_semicolons(code):
        body = chunk.strip()
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


# ── Migrations for databases created before a column/index existed ──────────
# `CREATE TABLE IF NOT EXISTS` never alters an existing table, so additive
# schema changes need an explicit, idempotent step here. MySQL has no
# `ADD COLUMN IF NOT EXISTS`, hence the information_schema probes.
def _migrate() -> None:
    with engine.begin() as conn:
        has_col = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'products' "
                "AND column_name = 'name_norm'"
            )
        ).scalar()
        if not has_col:
            print("  · adding products.name_norm (generated)", flush=True)
            conn.execute(
                text(
                    "ALTER TABLE products ADD COLUMN name_norm VARCHAR(255) "
                    "GENERATED ALWAYS AS (REGEXP_REPLACE(TRIM(name), "
                    "'[[:space:]]+', ' ')) STORED"
                )
            )

        has_idx = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'products' "
                "AND index_name = 'idx_products_name_norm'"
            )
        ).scalar()
        if not has_idx:
            print("  · adding idx_products_name_norm", flush=True)
            conn.execute(text("CREATE INDEX idx_products_name_norm ON products (name_norm)"))

        # promotions.club_id started at VARCHAR(32) — too small for the club
        # *expressions* some chains ship (105 chars observed at Rami Levy).
        club_len = conn.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'promotions' "
                "AND column_name = 'club_id'"
            )
        ).scalar()
        if club_len is not None and club_len < 255:
            print("  · widening promotions.club_id → VARCHAR(255)", flush=True)
            conn.execute(text("ALTER TABLE promotions MODIFY club_id VARCHAR(255) NULL"))


def main(max_retries: int = 8) -> None:
    sql = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(SCHEMA_DIR.glob("*.sql"))
    )
    statements = _statements(sql)
    print(f"init_db → applying {len(statements)} statements to "
          f"{engine.url.host}:{engine.url.port}/{engine.url.database}", flush=True)
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            _apply(statements)
            _migrate()
            print("✅ schema applied successfully", flush=True)
            return
        # DBAPIError covers sqlalchemy.exc.OperationalError / InterfaceError / etc.;
        # connection_invalidated is True for any dropped-connection variant.
        except DBAPIError as exc:
            transient = exc.connection_invalidated or _is_transient(exc)
            if attempt >= max_retries or not transient:
                raise
            code = exc.orig.args[0] if getattr(exc.orig, "args", None) else "?"
            print(f"Connection failed ({code}), retrying... "
                  f"({attempt + 1}/{max_retries}) in {delay:.0f}s", flush=True)
            engine.dispose()  # force a brand-new connection on the next attempt
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


if __name__ == "__main__":
    main()
