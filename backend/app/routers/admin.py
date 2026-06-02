"""Admin endpoints: login (bcrypt + JWT) and JWT-protected operations."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from .. import etl_runner, scheduler
from ..config import settings
from ..schemas import LoginRequest, TokenResponse
from ..security import create_access_token, get_current_admin, verify_password

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/login", response_model=TokenResponse, summary="Admin login → JWT (24h)")
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


@router.post(
    "/etl/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the local ETL now in the background (protected)",
)
def run_etl(
    background_tasks: BackgroundTasks,
    full: bool = False,
    admin: str = Depends(get_current_admin),
):
    """Kick off the local ETL (parse archive/ → upsert) via BackgroundTasks so
    the HTTP request returns immediately instead of blocking for minutes."""
    if etl_runner.is_running():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ETL is already running"
        )
    background_tasks.add_task(etl_runner.run_etl_job, full)
    return {
        "status": "started",
        "full": full,
        "message": "ETL started in the background",
    }


@router.get("/etl/status", summary="Manual-ETL run state (protected)")
def etl_status(admin: str = Depends(get_current_admin)):
    return etl_runner.get_state()
