"""Zolt FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import scheduler
from .config import settings
from .db import get_db
from .routers import admin, basket, products, stores


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the weekly Kaggle-ETL scheduler on boot (registers the cron job;
    # it does not download anything until the trigger fires).
    if settings.scheduler_enabled:
        scheduler.start_scheduler()
    try:
        yield
    finally:
        scheduler.shutdown_scheduler()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Compare supermarket basket prices across Shufersal, Rami Levy and Osher Ad.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,  # e.g. allow Vercel preview URLs
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products.router)
app.include_router(stores.router)
app.include_router(basket.router)
app.include_router(admin.router)


@app.get("/", tags=["meta"], summary="Service info")
def root():
    return {"service": settings.app_name, "version": app.version, "docs": "/docs"}


@app.get("/health", tags=["meta"], summary="Health check (incl. DB connectivity)")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "up"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
