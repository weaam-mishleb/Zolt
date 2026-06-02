"""Zolt FastAPI application entry point."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .routers import basket, products, stores

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Compare supermarket basket prices across Shufersal, Rami Levy and Osher Ad.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products.router)
app.include_router(stores.router)
app.include_router(basket.router)


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
