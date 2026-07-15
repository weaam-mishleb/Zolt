"""Trigger the heavy ETL workflow on GitHub Actions via the REST API.

The free Render instance (512MB RAM) cannot run the full ETL in-process, so
the admin trigger dispatches `.github/workflows/etl.yml` to a GitHub runner
instead (`workflow_dispatch`). The runner reports progress into the shared
`etl_jobs` MySQL table, which the admin panel polls through this backend.

Requires GH_PAT (fine-grained PAT with "Actions: Read and write" on the repo)
and GH_REPO ("owner/repo") — see .env.example.
"""
from __future__ import annotations

import httpx

from .config import settings

_API_VERSION = "2022-11-28"


def is_configured() -> bool:
    return bool(settings.gh_pat and settings.gh_repo)


def dispatch_etl(job_id: int, full: bool) -> None:
    """POST a workflow_dispatch event. GitHub answers 204 on success; anything
    else raises httpx.HTTPStatusError for the caller to translate."""
    url = (
        f"https://api.github.com/repos/{settings.gh_repo}"
        f"/actions/workflows/{settings.gh_workflow}/dispatches"
    )
    resp = httpx.post(
        url,
        json={
            "ref": settings.gh_ref,
            "inputs": {"job_id": str(job_id), "full": "true" if full else "false"},
        },
        headers={
            "Authorization": f"Bearer {settings.gh_pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
