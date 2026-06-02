"""Application settings, loaded from environment / the project-root .env file."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Zolt/ project root (this file is backend/app/config.py)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Read the project-root .env (same file docker-compose uses). Unknown keys
    # (MYSQL_*) are ignored so a single .env can serve DB + backend.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "zolt"
    db_user: str = "zolt"
    db_password: str = "zolt_pass"

    # Full connection string — managed hosts (Render/Railway) inject DATABASE_URL.
    # When set, it overrides the individual DB_* parts above.
    database_url_override: str | None = Field(default=None, validation_alias="DATABASE_URL")

    # Connection-pool tuning (SQLAlchemy QueuePool)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 3600  # seconds; recycle stale conns (cloud DBs drop idle ones)
    db_pool_timeout: int = 30
    # PyMySQL socket timeouts (connect_args) — generous for a high-latency WAN to a
    # cloud DB. A read/write that stalls past these raises 2013 → the loader retries.
    db_connect_timeout: int = 60
    db_read_timeout: int = 120
    db_write_timeout: int = 120

    # ── API / CORS ───────────────────────────────────────────
    app_name: str = "Zolt API"
    # Comma-separated allowed origins (add your Vercel domain in production).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Optional regex to also allow Vercel preview URLs, e.g. https://.*\.vercel\.app$
    cors_origin_regex: str | None = None

    # ── Admin auth (JWT) ─────────────────────────────────────
    jwt_secret: str = "dev-only-change-me-to-a-long-random-secret"
    jwt_expire_hours: int = 1
    admin_username: str = "admin"
    admin_password_hash: str = ""  # bcrypt hash; empty ⇒ login disabled

    # ── Kaggle / automated ETL ───────────────────────────────
    # Dataset reference "owner/dataset-name" on Kaggle.
    kaggle_dataset: str = "erlichsefi/israeli-supermarkets-data"
    # Directory that holds kaggle.json (KAGGLE_CONFIG_DIR for the kaggle pkg).
    kaggle_config_dir: str = str(PROJECT_ROOT / "secrets")
    # Weekly schedule (Sunday 03:00 local time).
    etl_cron_day_of_week: str = "sun"
    etl_cron_hour: int = 3
    etl_cron_minute: int = 0
    scheduler_enabled: bool = True

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        # Managed providers give a single URL (often `mysql://…`); normalize it
        # to the PyMySQL driver and ensure utf8mb4 so Hebrew is preserved.
        if self.database_url_override:
            url = self.database_url_override.strip()
            if url.startswith("mysql://"):
                url = "mysql+pymysql://" + url[len("mysql://") :]
            if "charset=" not in url:
                url += ("&" if "?" in url else "?") + "charset=utf8mb4"
            return url
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


settings = Settings()
