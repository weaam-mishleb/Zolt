"""ETL job tracking — one `etl_jobs` row per run, updated live while loading.

The run may execute on a GitHub Actions runner while the admin panel polls the
FastAPI backend on Render; the shared MySQL database is the only channel
between them, so progress is written here and read by GET /admin/etl/status.

Progress writes are best-effort and throttled: a hiccup while reporting must
never kill a multi-minute load, and the DB shouldn't be hammered once per
chunk when the percentage hasn't moved.
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import text
from sqlalchemy.engine import Engine

_INSERT = text("INSERT INTO etl_jobs (source, is_full) VALUES (:source, :is_full)")
_LATEST = text(
    """
    SELECT id, source, status, progress, phase, is_full, detail,
           started_at, finished_at, updated_at
    FROM etl_jobs ORDER BY id DESC LIMIT 1
    """
)


def create_job(engine: Engine, *, source: str, full: bool) -> int:
    """Insert a queued job row and return its id."""
    with engine.begin() as conn:
        res = conn.execute(_INSERT, {"source": source, "is_full": int(full)})
        return int(res.lastrowid)


def get_latest_job(engine: Engine) -> dict | None:
    """The most recent job row (the admin status endpoint reads this)."""
    with engine.connect() as conn:
        row = conn.execute(_LATEST).mappings().first()
        return dict(row) if row else None


def mark_failed(engine: Engine, job_id: int, detail: str) -> None:
    """Fail a job from outside the run (e.g. the workflow dispatch call errored)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE etl_jobs SET status='failed', detail=:d, finished_at=NOW() "
                "WHERE id=:id"
            ),
            {"d": detail[:2000], "id": job_id},
        )


class ProgressReporter:
    """Throttled progress writer for a single job row.

    `update()` may be called once per streamed chunk; it only hits the DB when
    the integer percentage moved AND at least `min_interval` seconds passed
    (so a heartbeat is still recorded on slow phases).
    """

    def __init__(self, engine: Engine, job_id: int, min_interval: float = 2.0):
        self.engine = engine
        self.job_id = job_id
        self.min_interval = min_interval
        self._last_pct = -1
        self._last_write = 0.0

    def _write(self, sql: str, params: dict) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(text(sql), {**params, "id": self.job_id})
        except Exception as exc:  # noqa: BLE001 — progress is best-effort
            print(f"  ! progress write failed (ignored): {exc}", file=sys.stderr)

    def start(self, phase: str) -> None:
        self._write(
            "UPDATE etl_jobs SET status='running', progress=0, phase=:phase, "
            "started_at=NOW(), finished_at=NULL, detail=NULL WHERE id=:id",
            {"phase": phase},
        )

    def update(self, pct: int, phase: str | None = None) -> None:
        pct = max(0, min(99, int(pct)))  # 100 is reserved for finish()
        now = time.monotonic()
        if pct == self._last_pct or (now - self._last_write) < self.min_interval:
            return
        self._last_pct, self._last_write = pct, now
        if phase is not None:
            self._write(
                "UPDATE etl_jobs SET progress=:p, phase=:phase WHERE id=:id",
                {"p": pct, "phase": phase},
            )
        else:
            self._write("UPDATE etl_jobs SET progress=:p WHERE id=:id", {"p": pct})

    def finish(self, ok: bool, detail: str | None = None) -> None:
        self._write(
            "UPDATE etl_jobs SET status=:s, progress=:p, phase=NULL, detail=:d, "
            "finished_at=NOW() WHERE id=:id",
            {
                "s": "success" if ok else "failed",
                "p": 100 if ok else self._last_pct if self._last_pct >= 0 else 0,
                "d": (detail or "")[:2000] or None,
            },
        )
