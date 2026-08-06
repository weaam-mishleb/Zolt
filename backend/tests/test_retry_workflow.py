"""Tests for the runner-allocation retry workflow.

WHY THIS EXISTS
---------------
A run died with "The job was not acquired by Runner of type hosted even after
multiple attempts" during a GitHub capacity incident. The job never started, so
nothing inside etl-matrix.yml could have caught it: `timeout-minutes` measures a
job that is running, and step-level retry actions need a step to run in. The
only lever is re-running the failed jobs afterwards.

The dangerous part of that lever is silence. A `workflow_run` trigger matches the
target by its NAME string, so renaming the ETL workflow would stop the retry
firing without any error anywhere — the same class of quiet breakage this
project keeps getting bitten by. These tests pin the coupling and the loop guard.
"""
from __future__ import annotations

import pathlib

import yaml

WORKFLOWS = pathlib.Path(".github/workflows")


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return doc.get("on", doc.get(True))


def test_the_retry_watches_the_etl_workflow_by_its_exact_name():
    """THE coupling: a workflow_run trigger matches on the name string, so a
    rename silently stops the retry from ever firing."""
    etl_name = _load("etl-matrix.yml")["name"]
    watched = _triggers(_load("etl-retry.yml"))["workflow_run"]["workflows"]
    assert etl_name in watched, (
        f"etl-retry.yml watches {watched} but the ETL workflow is now called {etl_name!r}"
    )


def test_the_retry_only_fires_on_failure():
    guard = _load("etl-retry.yml")["jobs"]["rerun-failed"]["if"]
    assert "conclusion == 'failure'" in " ".join(guard.split())


def test_the_retry_is_bounded_so_it_cannot_loop():
    """`gh run rerun --failed` bumps the attempt on the SAME run, which fires
    workflow_run again on completion. Without a bound that is an infinite loop
    billed by the minute."""
    guard = " ".join(_load("etl-retry.yml")["jobs"]["rerun-failed"]["if"].split())
    assert "run_attempt < 3" in guard


def test_the_retry_has_the_permission_it_needs_and_no_more():
    perms = _load("etl-retry.yml")["permissions"]
    assert perms["actions"] == "write"          # required to re-run a run
    assert perms.get("contents") == "read"      # and nothing elevated beyond it
    assert set(perms) == {"actions", "contents"}


def test_the_retry_job_cannot_hang_forever():
    assert _load("etl-retry.yml")["jobs"]["rerun-failed"]["timeout-minutes"] <= 30


def test_the_etl_matrix_still_isolates_chains_from_each_other():
    """Auto-retrying is only safe because a failing chain cannot take others
    down, and because re-running a chain is idempotent."""
    strategy = _load("etl-matrix.yml")["jobs"]["load"]["strategy"]
    assert strategy["fail-fast"] is False


def test_the_etl_matrix_still_accepts_a_chain_subset():
    """The manual recovery path the retry's failure message points at."""
    inputs = _triggers(_load("etl-matrix.yml"))["workflow_dispatch"]["inputs"]
    assert "chains" in inputs
