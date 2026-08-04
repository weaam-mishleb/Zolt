"""Say WHICH layer of the database connection is broken, in seconds.

WHY THIS EXISTS
---------------
A dead database reports itself as

    (2013, 'Lost connection to MySQL server during query')

which reads like a query problem and is nothing of the sort. The same message
covers a refused socket, a proxy with nothing behind it, a TLS mismatch and a
genuinely dropped query, so a real outage sent us hunting through connection
pools, deadlocks and isolation levels before anyone checked whether MySQL was
answering at all. It cost a full run, then a second one.

The layers fail differently and only one of them is ours to fix:

    DNS      → the host does not resolve; the URL or the secret is wrong
    TCP      → refused/timeout; wrong port, firewall, or nothing listening
    HANDSHAKE→ socket opens, then closes with no greeting. The managed proxy is
               up but no MySQL is behind it: the service is stopped, asleep,
               crashed, or out of quota. NOT a client problem — no pool size,
               retry policy or isolation level changes this.
    AUTH     → MySQL answered and rejected the credentials
    OK       → reachable

Usage:
    python -m scripts.db_probe                # reads DATABASE_URL / .env
    python -m scripts.db_probe --github       # emits ::error:: annotations
"""
from __future__ import annotations

import argparse
import socket
import sys
import time

from sqlalchemy import make_url

# The greeting is the first thing a live MySQL sends. Waiting on it is what
# separates "proxy is up" from "database is up".
_GREETING_TIMEOUT = 10
_TCP_TIMEOUT = 10


def probe(url_str: str) -> tuple[str, str]:
    """Return (layer, detail). layer == 'OK' when the database is reachable."""
    url = make_url(url_str)
    host, port = url.host, url.port or 3306
    if not host:
        return "DNS", "the connection URL has no host"

    try:
        socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        return "DNS", f"{host} does not resolve ({exc})"

    t0 = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=_TCP_TIMEOUT)
    except OSError as exc:
        return "TCP", f"cannot open a socket to {host}:{port} ({exc})"

    try:
        sock.settimeout(_GREETING_TIMEOUT)
        try:
            first = sock.recv(4)
        except socket.timeout:
            return "HANDSHAKE", (
                f"{host}:{port} accepted the socket but sent no MySQL greeting "
                f"within {_GREETING_TIMEOUT}s"
            )
        if not first:
            return "HANDSHAKE", (
                f"{host}:{port} accepted the socket and closed it without a MySQL "
                f"greeting (after {time.time() - t0:.2f}s)"
            )
    finally:
        sock.close()

    # A greeting arrived, so the server is real; now let the driver finish.
    try:
        import pymysql

        conn = pymysql.connect(
            host=host, port=port, user=url.username, password=url.password,
            database=url.database, connect_timeout=_TCP_TIMEOUT,
        )
        conn.close()
    except Exception as exc:  # noqa: BLE001 — the message is the whole point
        code = exc.args[0] if getattr(exc, "args", None) else ""
        return "AUTH", f"MySQL answered but refused the connection ({code}: {exc})"

    return "OK", f"{host}:{port} is serving MySQL"


_ADVICE = {
    "DNS": "Check DATABASE_URL / the repository secret — the hostname is wrong or gone.",
    "TCP": "Nothing is listening on that port. Check the host/port and any firewall.",
    "HANDSHAKE": (
        "The proxy is up but NO DATABASE IS BEHIND IT. On Railway this means the MySQL "
        "service is stopped, asleep, crashed, or out of quota/credits — open the Railway "
        "dashboard and check the service is running. This is not a client problem: pool "
        "size, retries and isolation level cannot fix it."
    ),
    "AUTH": "The database is alive. The credentials or the database name are wrong.",
}


def main() -> None:
    p = argparse.ArgumentParser(description="Diagnose which layer of the DB connection fails")
    p.add_argument("--github", action="store_true", help="emit ::error:: annotations")
    args = p.parse_args()

    from backend.app.config import settings

    layer, detail = probe(settings.database_url)

    if layer == "OK":
        print(f"✅ database reachable — {detail}", flush=True)
        return

    prefix = "::error::" if args.github else "❌ "
    print(f"{prefix}database unreachable at the {layer} layer: {detail}", file=sys.stderr)
    print(_ADVICE[layer], file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
