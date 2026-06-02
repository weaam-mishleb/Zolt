"""Create the Zolt tables in the database the current settings point at.

Useful for managed hosts (Render/Railway/Aiven) where the docker-compose init
script doesn't run. It skips the `CREATE DATABASE` / `USE` / `SET NAMES` lines so
it targets whatever database DATABASE_URL already points to.

Run (from the project root):
    DATABASE_URL='mysql://user:pass@host:port/db' python -m scripts.init_db
"""
from __future__ import annotations

from sqlalchemy import text

from backend.app.config import PROJECT_ROOT
from backend.app.db import engine

SCHEMA = PROJECT_ROOT / "db" / "init" / "01_schema.sql"
_SKIP_PREFIXES = ("create database", "use ", "set names")


def _statements(sql: str) -> list[str]:
    out: list[str] = []
    for chunk in sql.split(";"):
        body = "\n".join(
            ln for ln in chunk.splitlines() if not ln.strip().startswith("--")
        ).strip()
        if body and not body.lower().startswith(_SKIP_PREFIXES):
            out.append(body)
    return out


def main() -> None:
    statements = _statements(SCHEMA.read_text(encoding="utf-8"))
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print(f"✅ applied {len(statements)} statements to "
          f"{engine.url.host}/{engine.url.database}")


if __name__ == "__main__":
    main()
