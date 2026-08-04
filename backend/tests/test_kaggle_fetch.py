"""Tests for classifying and retrying Kaggle downloads.

WHY THIS EXISTS
---------------
The workflow used to decide by grepping for "404", which splits the outcomes in
two when there are three. A throttled download fails with

    Expecting value: line 1 column 1 (char 0)

— a JSONDecodeError, because the client asked for JSON and Kaggle's edge
returned an HTML error page for a 429 or 5xx. Lumping that in with "real
failure" turned a rate limit into a dead chain; lumping it in with "404" would
be worse, silently dropping a chain that is perfectly healthy.
"""
from __future__ import annotations

import pytest

from scripts.kaggle_fetch import FATAL, MISSING, OK, TRANSIENT, backoff, classify, fetch


# ── classification ──────────────────────────────────────────────────────────
def test_the_json_decode_error_is_transient():
    """THE case: this is a 429/5xx wearing a parser error's clothes."""
    assert classify("Expecting value: line 1 column 1 (char 0)") == TRANSIENT


@pytest.mark.parametrize("text", [
    "429 Client Error: Too Many Requests",
    "503 Service Unavailable",
    "502 Bad Gateway",
    "ConnectionResetError: connection reset by peer",
    "HTTPSConnectionPool: Read timed out",
    "RemoteDisconnected: Remote end closed connection without response",
])
def test_throttles_and_hiccups_are_transient(text):
    assert classify(text) == TRANSIENT


def test_a_genuine_404_is_missing():
    assert classify("404 Client Error: Not Found for url: https://api.kaggle.com/...") == MISSING


def test_anything_else_is_fatal():
    assert classify("401 Client Error: Unauthorized") == FATAL
    assert classify("Could not find kaggle.json") == FATAL


def test_a_throttle_mentioning_not_found_is_still_transient():
    """Precedence matters. An HTML throttle page can contain the words "not
    found"; reading that as "this chain does not exist" would silently drop a
    healthy chain, which is the more expensive mistake."""
    html = "<html><title>429 Too Many Requests</title><body>not found</body></html>"
    assert classify(html) == TRANSIENT


# ── backoff ─────────────────────────────────────────────────────────────────
def test_backoff_grows_and_is_capped_and_jittered():
    assert all(1.0 <= backoff(0, base=2.0, cap=32.0) <= 2.0 for _ in range(50))
    assert all(2.0 <= backoff(1, base=2.0, cap=32.0) <= 4.0 for _ in range(50))
    assert all(16.0 <= backoff(9, base=2.0, cap=32.0) <= 32.0 for _ in range(50))
    assert len({backoff(3) for _ in range(200)}) > 100      # actually randomised


# ── the retry loop ──────────────────────────────────────────────────────────
class _FakeProc:
    def __init__(self, rc, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


def _runner(sequence, calls):
    """subprocess.run stand-in that walks `sequence` of (rc, stderr)."""
    def run(*_a, **_kw):
        calls.append(1)
        rc, err = sequence[min(len(calls) - 1, len(sequence) - 1)]
        return _FakeProc(rc, err)
    return run


def test_a_transient_failure_is_retried_until_it_succeeds(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        _runner([(1, "Expecting value: line 1 column 1 (char 0)"),
                 (1, "429 Too Many Requests"),
                 (0, "")], calls),
    )
    assert fetch("d", "f.csv", "dest", attempts=5, sleep=lambda _s: None) == OK
    assert len(calls) == 3


def test_a_404_is_not_retried(monkeypatch):
    """Retrying an absent file just burns a minute before the same answer."""
    calls = []
    monkeypatch.setattr("subprocess.run", _runner([(1, "404 Not Found")], calls))
    assert fetch("d", "f.csv", "dest", attempts=5, sleep=lambda _s: None) == MISSING
    assert len(calls) == 1


def test_a_fatal_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", _runner([(1, "401 Unauthorized")], calls))
    assert fetch("d", "f.csv", "dest", attempts=5, sleep=lambda _s: None) == FATAL
    assert len(calls) == 1


def test_endless_throttling_eventually_gives_up_as_fatal(monkeypatch):
    """It must not retry forever and must not report success."""
    calls = []
    monkeypatch.setattr("subprocess.run", _runner([(1, "429 Too Many Requests")], calls))
    assert fetch("d", "f.csv", "dest", attempts=3, sleep=lambda _s: None) == FATAL
    assert len(calls) == 3


def test_a_first_try_success_makes_no_extra_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", _runner([(0, "")], calls))
    assert fetch("d", "f.csv", "dest", sleep=lambda _s: None) == OK
    assert len(calls) == 1
