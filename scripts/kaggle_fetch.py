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
import random
import subprocess
import sys
import time

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


def fetch(dataset: str, filename: str, dest: str, attempts: int = 5, sleep=time.sleep) -> str:
    """Download `filename`, retrying only the transient failures."""
    for attempt in range(attempts):
        proc = subprocess.run(
            ["kaggle", "datasets", "download", "-d", dataset, "-f", filename,
             "-p", dest, "--force", "--unzip"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return OK

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
