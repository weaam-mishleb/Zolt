"""Application settings, loaded from environment / the project-root .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Connection-pool tuning (SQLAlchemy QueuePool)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800  # seconds; recycle before MySQL wait_timeout
    db_pool_timeout: int = 30

    # ── API ──────────────────────────────────────────────────
    app_name: str = "Zolt API"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


settings = Settings()
