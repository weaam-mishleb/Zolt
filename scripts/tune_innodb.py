"""Raise the InnoDB buffer pool at runtime, before the loaders start.

WHY THIS EXISTS
---------------
The managed instance ships with `innodb_buffer_pool_size = 1 GB` against a
1,619 MB working set (`prices` alone is 1,068 MB), on a container with 24 GB of
RAM. Every upsert therefore probes an index that is not cached, and throughput
collapsed to ~300 rows/s — slow enough that GitHub cancelled the big chains at
the job timeout.

The variable is dynamic, so this needs no config file and no restart: MySQL
resizes the pool online. The catch is that persisting it IS hard on a managed
host — a container restart puts it back to 1 GB — which is why this runs at the
start of every workflow rather than once by hand.

Deliberately best-effort. If the grant is missing or the host refuses, this
warns and exits 0: a slower load is bad, a load that will not start because a
TUNING step failed is worse.

Usage:
    python -m scripts.tune_innodb                 # target 8 GiB
    python -m scripts.tune_innodb --gib 4
    python -m scripts.tune_innodb --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import text

GIB = 1024**3
DEFAULT_TARGET_GIB = 8
# InnoDB reports progress here; the SET returns immediately but the resize runs
# in the background, and starting a 1.3M-row load into a pool that is still
# growing would waste the entire point of doing this first.
_RESIZE_TIMEOUT_S = 180
_DONE_MARKERS = ("completed", "resizing completed", "not started")


def _vars(conn, names: tuple[str, ...]) -> dict[str, str]:
    rows = conn.execute(
        text("SHOW GLOBAL VARIABLES WHERE Variable_name IN :n").bindparams(
            __import__("sqlalchemy").bindparam("n", expanding=True)
        ),
        {"n": list(names)},
    ).all()
    return {k: v for k, v in rows}


def valid_target(requested: int, chunk: int, instances: int, current: int) -> int | None:
    """Round `requested` down to a size InnoDB will actually accept.

    The pool must be a whole number of (chunk_size x instances) — 512 MB on this
    host. MySQL silently rounds a bad value, so computing it here is what makes
    the resulting log honest rather than merely optimistic.

    Returns None when there is nothing worth doing: never shrink a pool that is
    already larger, since that would evict exactly the pages this exists to keep.
    """
    granule = chunk * max(instances, 1)
    target = (requested // granule) * granule
    if target <= current:
        return None
    return target


def _wait_for_resize(engine, target: int) -> str:
    deadline = time.monotonic() + _RESIZE_TIMEOUT_S
    status = ""
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            status = conn.execute(
                text("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_resize_status'")
            ).all()
            status = status[0][1] if status else ""
            now = int(_vars(conn, ("innodb_buffer_pool_size",))["innodb_buffer_pool_size"])
        if now >= target and (not status or any(m in status.lower() for m in _DONE_MARKERS)):
            return status
        time.sleep(2)
    return status or "(timed out waiting for the resize to finish)"


def main() -> None:
    p = argparse.ArgumentParser(description="Raise the InnoDB buffer pool for the ETL run")
    p.add_argument("--gib", type=int, default=DEFAULT_TARGET_GIB, help="target size in GiB")
    p.add_argument("--dry-run", action="store_true", help="report only")
    args = p.parse_args()

    from backend.app.db import engine

    try:
        with engine.connect() as conn:
            v = _vars(conn, (
                "innodb_buffer_pool_size",
                "innodb_buffer_pool_chunk_size",
                "innodb_buffer_pool_instances",
            ))
        current = int(v["innodb_buffer_pool_size"])
        chunk = int(v["innodb_buffer_pool_chunk_size"])
        instances = int(v["innodb_buffer_pool_instances"])
    except Exception as exc:  # noqa: BLE001 — tuning must never block the load
        print(f"::warning::could not read InnoDB settings, skipping tuning: {exc}", file=sys.stderr)
        return

    target = valid_target(args.gib * GIB, chunk, instances, current)
    print(f"  buffer pool now : {current / 1024 / 1024:,.0f} MB "
          f"(chunk {chunk / 1024 / 1024:,.0f} MB x {instances} instances)")

    if target is None:
        print(f"  ✅ already at or above the {args.gib} GiB target — nothing to do")
        return

    print(f"  target          : {target / 1024 / 1024:,.0f} MB")
    if args.dry_run:
        print("  DRY RUN — nothing changed")
        return

    try:
        with engine.begin() as conn:
            conn.execute(text(f"SET GLOBAL innodb_buffer_pool_size = {target}"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"::warning::could not resize the buffer pool ({exc}). The load will still run, "
            f"but expect the throughput that made the big chains time out.",
            file=sys.stderr,
        )
        return

    status = _wait_for_resize(engine, target)
    with engine.connect() as conn:
        final = int(_vars(conn, ("innodb_buffer_pool_size",))["innodb_buffer_pool_size"])

    print(f"  buffer pool now : {final / 1024 / 1024:,.0f} MB  [{status}]")
    if final < target:
        print(
            "::warning::the resize did not reach the target — the host may cap it. "
            "Loading anyway.",
            file=sys.stderr,
        )
    else:
        print(f"  ✅ buffer pool raised to {final / 1024 / 1024 / 1024:.0f} GiB for this run")


if __name__ == "__main__":
    main()
