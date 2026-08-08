"""Download one file from a Kaggle dataset, telling the three outcomes apart.

WHY THIS EXISTS
---------------
The workflow used to classify a download by grepping its output for "404". That
splits the world in two, and the world has three parts:

  MISSING    the upstream scraper did not publish this chain today. Nothing is
             wrong; there is nothing to load. Skip the chain.
  TRANSIENT  Kaggle throttled or hiccuped. The give-away is
                 Expecting value: line 1 column 1 (char 0)
             which is a JSONDecodeError: the client asked for JSON and got an
             HTML error page — a 429 or a 5xx from the edge, not from the API.
             Retrying works; failing the chain does not.
  FATAL      credentials, a wrong dataset ref, a broken client. Must stay loud.

Grouping TRANSIENT with FATAL is what turned a rate limit into a red chain.

The status code is deliberately NOT parsed out: the Kaggle CLI does not surface
it, and reaching for it would mean replacing the client with raw HTTP — a whole
auth and pagination layer — to learn something that changes nothing. 429 and 503
and a truncated JSON body all want the same response: wait longer, try again.

Exit codes (the workflow branches on these):
    0  file downloaded
    3  file is not in the dataset (skip the chain)
    1  everything else
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path, PurePosixPath

MISSING = "missing"
TRANSIENT = "transient"
FATAL = "fatal"
OK = "ok"

# Matched against the CLI's combined output, lowercased.
_MISSING_SIGNS = ("404", "not found")
_TRANSIENT_SIGNS = (
    "expecting value",          # JSONDecodeError — an HTML error page, not JSON
    "429",
    "too many requests",
    "500", "502", "503", "504",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "connection reset",
    "connection aborted",
    "read timed out",
    "timed out",
    "temporarily unavailable",
    "remote end closed",
)

_INDEX_CACHE = ".kaggle-file-index.json"


def classify(output: str) -> str:
    """MISSING / TRANSIENT / FATAL from a failed download's output.

    TRANSIENT is checked FIRST. A throttled response can mention a 404-ish
    string in an unrelated HTML body, and treating a rate limit as "this chain
    does not exist" would silently drop a chain that is perfectly healthy —
    the more expensive mistake of the two.
    """
    low = output.lower()
    if any(s in low for s in _TRANSIENT_SIGNS):
        return TRANSIENT
    if any(s in low for s in _MISSING_SIGNS):
        return MISSING
    return FATAL


def backoff(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    """Exponential with jitter over the upper half — a throttle needs real time
    to clear, and every runner is being throttled by the same edge at once."""
    window = min(base * (2**attempt), cap)
    return random.uniform(window / 2, window)


def _parse_file_page(output: str) -> tuple[list[str], str | None]:
    """Parse one ``kaggle datasets files --csv`` page.

    Kaggle 2.x writes both its upgrade warning and ``Next Page Token = ...``
    above the CSV header, so feeding the whole output to DictReader silently
    interprets the warning as the header and returns no paths.
    """
    lines = output.splitlines()
    token = None
    for line in lines:
        if line.startswith("Next Page Token = "):
            token = line.removeprefix("Next Page Token = ").strip() or None

    try:
        header = next(i for i, line in enumerate(lines) if line.startswith("name,"))
    except StopIteration as exc:
        raise ValueError("Kaggle file listing contained no CSV header") from exc

    rows = csv.DictReader(StringIO("\n".join(lines[header:])))
    paths = [row["name"].strip() for row in rows if row.get("name", "").strip()]
    return paths, token


def _listing_page(
    dataset: str,
    page_token: str | None,
    *,
    attempts: int,
    sleep,
) -> tuple[str, subprocess.CompletedProcess | None]:
    command = [
        "kaggle", "datasets", "files", dataset,
        "--csv", "--page-size", "200",
    ]
    if page_token:
        command.extend(("--page-token", page_token))

    for attempt in range(attempts):
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode == 0:
            return OK, proc

        combined = f"{proc.stdout}\n{proc.stderr}"
        verdict = classify(combined)
        print(combined.strip(), file=sys.stderr)
        # A listing-level 404 means the dataset/ref is broken, not that one
        # requested file is absent. It must therefore stay fatal.
        if verdict != TRANSIENT:
            return FATAL, None
        if attempt == attempts - 1:
            return FATAL, None
        sleep(backoff(attempt))
    return FATAL, None


def list_remote_paths(
    dataset: str,
    dest: str,
    *,
    attempts: int = 5,
    sleep=time.sleep,
) -> tuple[str, list[str]]:
    """List every remote path once per job and cache it beside downloads."""
    destination = Path(dest)
    cache_path = destination / _INDEX_CACHE
    try:
        cached = json.loads(cache_path.read_text("utf-8"))
        if cached.get("dataset") == dataset and isinstance(cached.get("paths"), list):
            return OK, cached["paths"]
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        pass

    paths: list[str] = []
    page_token = None
    while True:
        verdict, proc = _listing_page(
            dataset,
            page_token,
            attempts=attempts,
            sleep=sleep,
        )
        if verdict != OK or proc is None:
            return FATAL, []
        try:
            page_paths, page_token = _parse_file_page(proc.stdout)
        except ValueError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return FATAL, []
        paths.extend(page_paths)
        if not page_token:
            break

    destination.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"dataset": dataset, "paths": paths}, ensure_ascii=False)
    cache_path.write_text(payload, encoding="utf-8")
    return OK, paths


def resolve_remote_path(
    dataset: str,
    filename: str,
    dest: str,
    *,
    attempts: int = 5,
    sleep=time.sleep,
) -> tuple[str, str | None]:
    """Resolve a requested basename to the dataset's current nested path."""
    verdict, paths = list_remote_paths(dataset, dest, attempts=attempts, sleep=sleep)
    if verdict != OK:
        return verdict, None

    requested = PurePosixPath(filename).as_posix()
    if requested in paths:                  # old/root layout remains supported
        return OK, requested

    basename = PurePosixPath(filename).name
    matches = [path for path in paths if PurePosixPath(path).name == basename]
    if not matches:
        return MISSING, None
    if len(matches) > 1:
        print(
            f"::error::{basename} is ambiguous in {dataset}: {matches}",
            file=sys.stderr,
        )
        return FATAL, None
    return OK, matches[0]


def _flatten_download(remote_path: str, filename: str, dest: str) -> str:
    """Move a nested Kaggle extraction to the root filename loaders expect."""
    destination = Path(dest)
    target = destination / PurePosixPath(filename).name
    nested = destination.joinpath(*PurePosixPath(remote_path).parts)

    # Kaggle versions differ: some preserve the archive path, others flatten a
    # single-file extraction. Prefer the just-downloaded nested file when both
    # exist so a stale root copy can never win.
    if nested != target and nested.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        nested.replace(target)

    if not target.is_file():
        print(
            f"::error::Kaggle reported success but {target} was not extracted",
            file=sys.stderr,
        )
        return FATAL
    return OK


def fetch(dataset: str, filename: str, dest: str, attempts: int = 5, sleep=time.sleep) -> str:
    """Resolve and download `filename`, retrying only transient failures."""
    verdict, remote_path = resolve_remote_path(
        dataset,
        filename,
        dest,
        attempts=attempts,
        sleep=sleep,
    )
    if verdict != OK or remote_path is None:
        return verdict
    if remote_path != filename:
        print(f"· resolved {filename} → {remote_path}")

    for attempt in range(attempts):
        proc = subprocess.run(
            ["kaggle", "datasets", "download", "-d", dataset, "-f", remote_path,
             "-p", dest, "--force", "--unzip"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return _flatten_download(remote_path, filename, dest)

        combined = f"{proc.stdout}\n{proc.stderr}"
        verdict = classify(combined)
        print(combined.strip(), file=sys.stderr)

        if verdict != TRANSIENT:
            return verdict
        if attempt == attempts - 1:
            print(
                f"::error::{filename}: still failing after {attempts} attempts — "
                f"Kaggle is refusing or throttling this download",
                file=sys.stderr,
            )
            return FATAL

        delay = backoff(attempt)
        print(
            f"::warning::{filename}: transient Kaggle failure — "
            f"retry {attempt + 1}/{attempts - 1} in {delay:.1f}s",
            file=sys.stderr,
        )
        sleep(delay)
    return FATAL


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch one file from a Kaggle dataset")
    p.add_argument("--dataset", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--dest", default="archive")
    p.add_argument("--attempts", type=int, default=5)
    args = p.parse_args()

    verdict = fetch(args.dataset, args.file, args.dest, attempts=args.attempts)
    if verdict == OK:
        print(f"· downloaded {args.file}")
        sys.exit(0)
    if verdict == MISSING:
        print(f"::warning::{args.file} is not in the dataset today (404)")
        sys.exit(3)
    sys.exit(1)


if __name__ == "__main__":
    main()
