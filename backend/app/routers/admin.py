"""Admin endpoints: login (bcrypt + JWT) and JWT-protected operations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from .. import etl_runner, github_dispatch, scheduler
from ..config import settings
from ..db import engine
from ..schemas import LoginRequest, TokenResponse
from ..security import create_access_token, get_current_admin, verify_password

router = APIRouter(prefix="/admin", tags=["admin"])

# A queued/running job whose row hasn't been touched for this long is treated
# as dead (runner crashed / dispatch never picked up), not as in progress.
_STALE_AFTER = timedelta(minutes=15)


@router.post("/login", response_model=TokenResponse, summary="Admin login → JWT")
def login(body: LoginRequest):
    valid = (
        body.username == settings.admin_username
        and verify_password(body.password, settings.admin_password_hash)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token = create_access_token(body.username)
    return TokenResponse(access_token=token, expires_in=settings.jwt_expire_hours * 3600)


@router.get("/me", summary="Who am I (protected)")
def me(admin: str = Depends(get_current_admin)):
    return {"username": admin, "role": "admin"}


@router.get("/scheduler", summary="Scheduler status + next run (protected)")
def scheduler_status(admin: str = Depends(get_current_admin)):
    return scheduler.get_status()


@router.post(
    "/etl/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger the Kaggle download + ETL now (protected)",
)
def trigger_etl(admin: str = Depends(get_current_admin)):
    try:
        job_id = scheduler.trigger_now()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return {"status": "accepted", "job_id": job_id}


def _latest_job() -> dict | None:
    """Latest etl_jobs row, or None if the table is missing / DB hiccuped."""
    try:
        from etl.progress import get_latest_job

        return get_latest_job(engine)
    except Exception:  # noqa: BLE001 — status must degrade, not 500
        return None


def _is_stale(job: dict) -> bool:
    if job["status"] not in ("queued", "running"):
        return False
    updated = job.get("updated_at")
    if updated is None:
        return True
    now = datetime.now(timezone.utc) if updated.tzinfo else datetime.utcnow()
    return (now - updated) > _STALE_AFTER


@router.post(
    "/etl/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the ETL now — GitHub Actions dispatch, or in-process in dev (protected)",
)
def run_etl(
    background_tasks: BackgroundTasks,
    full: bool = False,
    admin: str = Depends(get_current_admin),
):
    """Production (GH_PAT + GH_REPO set): insert an etl_jobs row and dispatch the
    heavy workflow to a GitHub runner — the free Render instance (512MB RAM)
    cannot run the full load in-process. Without GH config (local dev), the ETL
    runs in the background via BackgroundTasks as before."""
    job = _latest_job()
    if job and job["status"] in ("queued", "running") and not _is_stale(job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ETL is already running"
        )

    if github_dispatch.is_configured():
        from etl.progress import create_job, mark_failed

        job_id = create_job(engine, source="github", full=full)
        try:
            github_dispatch.dispatch_etl(job_id, full)
        except httpx.HTTPError as exc:
            detail = f"Workflow dispatch failed: {exc}"
            try:
                mark_failed(engine, job_id, detail)
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
        return {
            "status": "queued",
            "mode": "github",
            "job_id": job_id,
            "full": full,
            "message": "ETL dispatched to GitHub Actions",
        }

    # Local dev fallback — no GitHub credentials configured. Refused on Render:
    # the 512MB instance must never run the ETL in-process, and a hung DB link
    # would strand the background task and block graceful shutdown.
    if settings.on_render:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ETL dispatch not configured — set GH_PAT and GH_REPO on Render",
        )
    if etl_runner.is_running():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ETL is already running"
        )
    background_tasks.add_task(etl_runner.run_etl_job, full)
    return {
        "status": "started",
        "mode": "local",
        "full": full,
        "message": "ETL started in the background",
    }


@router.get("/etl/status", summary="ETL run state + live progress % (protected)")
def etl_status(admin: str = Depends(get_current_admin)):
    """Merged view: the latest etl_jobs row (written live by the run itself,
    wherever it executes — GitHub runner or local) plus the legacy in-process
    runner state, so the admin panel needs a single poll target."""
    local = etl_runner.get_state()
    job = _latest_job()

    if job is None:
        return {
            "running": local["running"],
            "status": "running" if local["running"] else "idle",
            "progress": None,
            "phase": None,
            "job_id": None,
            "full": local.get("full"),
            "detail": local.get("error"),
            "started_at": local.get("started_at"),
            "finished_at": local.get("finished_at"),
            "source": "local" if local.get("started_at") else None,
            "local": local,
        }

    stale = _is_stale(job)
    running = job["status"] in ("queued", "running") and not stale
    return {
        "running": running or local["running"],
        "status": "stale" if stale else job["status"],
        "progress": int(job["progress"]),
        "phase": job["phase"],
        "job_id": job["id"],
        "full": bool(job["is_full"]),
        "detail": job["detail"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "source": job["source"],
        "local": local,
    }
