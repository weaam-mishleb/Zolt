"""Zolt ETL package.

DB POOL DEFAULTS — set here, before anything imports the engine.

`backend.app.db` builds one engine from `Settings`, whose pool defaults
(pool_size=10, max_overflow=20) are sized for the FastAPI app, which serves many
requests at once. The ETL is single-threaded: it upserts one batch at a time and
never needs more than one connection. Inheriting the web defaults let a single
runner open up to 30 connections, and the matrix runs six of them — up to 180
against one managed MySQL.

That is how a run dies. Past the server's connection limit the proxy stops
completing handshakes, PyMySQL raises 2013 out of `_get_server_information`, and
the retry loop's `engine.dispose()` turns it into a reconnect storm: every retry
drops live connections and immediately asks for new ones.

Every ETL entry point (`etl.run`, `etl.canonical`, `etl.promotions`) imports the
engine lazily from inside a function, so this module body always runs first.
Real environment variables win — this only supplies a default.
"""
from __future__ import annotations

import os

os.environ.setdefault("DB_POOL_SIZE", "2")
os.environ.setdefault("DB_MAX_OVERFLOW", "0")
