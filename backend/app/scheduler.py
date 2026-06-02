"""APScheduler setup: weekly automated Kaggle download + ETL (Sunday 03:00)."""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings

log = logging.getLogger("zolt.scheduler")

WEEKLY_JOB_ID = "weekly_kaggle_etl"
_scheduler: BackgroundScheduler | None = None


def weekly_etl_job() -> None:
    """Job body — download the latest dataset and run the ETL."""
    from etl.refresh import refresh_from_kaggle  # lazy: avoids importing kaggle at startup

    log.info("weekly ETL job started")
    try:
        summary = refresh_from_kaggle(
            dataset=settings.kaggle_dataset,
            config_dir=settings.kaggle_config_dir,
        )
        log.info("weekly ETL job finished: %s", summary)
    except Exception:  # noqa: BLE001 — never let a scheduler job crash the thread silently
        log.exception("weekly ETL job failed")


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler and register the weekly cron job (idempotent)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    sched = BackgroundScheduler()
    sched.add_job(
        weekly_etl_job,
        trigger=CronTrigger(
            day_of_week=settings.etl_cron_day_of_week,
            hour=settings.etl_cron_hour,
            minute=settings.etl_cron_minute,
        ),
        id=WEEKLY_JOB_ID,
        name="Weekly Kaggle ETL",
        max_instances=1,   # never overlap runs
        coalesce=True,     # collapse missed runs into one
        replace_existing=True,
    )
    sched.start()
    _scheduler = sched
    log.info(
        "scheduler started; weekly ETL on %s %02d:%02d",
        settings.etl_cron_day_of_week,
        settings.etl_cron_hour,
        settings.etl_cron_minute,
    )
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)


def get_status() -> dict:
    jobs = []
    if _scheduler:
        for j in _scheduler.get_jobs():
            jobs.append(
                {
                    "id": j.id,
                    "name": j.name,
                    "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
                }
            )
    return {
        "running": is_running(),
        "dataset": settings.kaggle_dataset,
        "schedule": f"{settings.etl_cron_day_of_week} "
        f"{settings.etl_cron_hour:02d}:{settings.etl_cron_minute:02d}",
        "jobs": jobs,
    }


def trigger_now() -> str:
    """Queue an immediate one-off run of the ETL job. Returns the job id."""
    if not is_running():
        raise RuntimeError("scheduler is not running")
    job_id = f"manual_etl_{int(datetime.now().timestamp())}"
    _scheduler.add_job(
        weekly_etl_job,
        trigger="date",  # run once, now
        id=job_id,
        name="Manual ETL (triggered)",
        max_instances=1,
    )
    return job_id
