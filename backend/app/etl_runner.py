"""In-process manual ETL runner used by the admin trigger (via BackgroundTasks).

A single-flight lock prevents overlapping runs; `_state` exposes progress so the
admin panel can poll GET /admin/etl/status.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "error": None,
    "full": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_running() -> bool:
    return _state["running"]


def get_state() -> dict:
    return dict(_state)


def run_etl_job(full: bool = False) -> None:
    """Run the local ETL once. Safe to hand to BackgroundTasks; no-ops if a run
    is already in progress."""
    if not _lock.acquire(blocking=False):
        return
    _state.update(running=True, started_at=_now(), finished_at=None, ok=None, error=None, full=full)
    try:
        from etl.run import run_pipeline

        run_pipeline(full=full)
        _state["ok"] = True
    except Exception as exc:  # noqa: BLE001 — record, don't crash the worker thread
        _state["ok"] = False
        _state["error"] = str(exc)
    finally:
        _state["finished_at"] = _now()
        _state["running"] = False
        _lock.release()
