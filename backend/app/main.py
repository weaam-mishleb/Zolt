"""Zolt FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import scheduler
from .config import settings
from .db import engine
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


# GET + HEAD: Render's health check (healthCheckPath: /) probes with HEAD,
# which a GET-only route rejects with 405 and the deploy times out.
@app.api_route("/", methods=["GET", "HEAD"], tags=["meta"], summary="Service info")
def root():
    return {"status": "ok", "service": settings.app_name, "version": app.version, "docs": "/docs"}


def _db_ping() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@app.api_route("/health", methods=["GET", "HEAD"], tags=["meta"],
               summary="Health check (incl. DB connectivity)")
async def health():
    # Hard 5s budget on the DB probe. A hung DB link (e.g. a black-holed
    # Render→Railway connection) must yield a fast 503 — an in-flight request
    # that never completes blocks uvicorn's graceful shutdown ("Waiting for
    # background tasks to complete") and leaves a zombie instance on Render.
    try:
        with anyio.fail_after(5):
            await anyio.to_thread.run_sync(_db_ping, abandon_on_cancel=True)
        return {"status": "ok", "db": "up"}
    except TimeoutError:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "timeout"})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
