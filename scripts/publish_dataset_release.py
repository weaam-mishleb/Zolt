"""Create (or reuse) a GitHub Release and upload the gzipped price files to it.

Reads the token from the GITHUB_TOKEN env var and the repo from GH_REPOSITORY
("owner/repo"). Idempotent and resumable: assets already present with a matching
size are skipped, so a dropped run can simply be re-run. Each upload is retried
with backoff (handy on a flaky/high-latency link).

    GITHUB_TOKEN=... GH_REPOSITORY=owner/repo python -m scripts.publish_dataset_release [tag]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GH_REPOSITORY"]
TAG = sys.argv[1] if len(sys.argv) > 1 else "dataset-v1"
API = "https://api.github.com"
FILES = [
    "archive/price_full_file_shufersal.csv.gz",
    "archive/price_full_file_rami_levy.csv.gz",
    "archive/price_full_file_osher_ad.csv.gz",
]
_HDRS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def api(url, method="GET", data=None, content_type=None, timeout=120):
    headers = dict(_HDRS)
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, (json.loads(body) if body else {})


def get_or_create_release():
    try:
        _, rel = api(f"{API}/repos/{REPO}/releases/tags/{TAG}")
        print(f"• release '{TAG}' already exists (id={rel['id']})")
        return rel
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    payload = json.dumps(
        {"tag_name": TAG, "name": "Dataset v1",
         "body": "Full price files for the Zolt cloud ETL (gzipped).", "draft": False}
    ).encode()
    _, rel = api(f"{API}/repos/{REPO}/releases", "POST", payload, "application/json")
    print(f"• created release '{TAG}' (id={rel['id']})")
    return rel


def upload(rel, path):
    name = Path(path).name
    size = os.path.getsize(path)
    assets = {a["name"]: a for a in rel.get("assets", [])}
    if name in assets and assets[name].get("size") == size:
        print(f"  ✓ {name} already present ({size / 1e6:.0f} MB) — skip")
        return
    if name in assets:  # stale/incomplete → remove and re-upload
        api(assets[name]["url"], "DELETE")
        print(f"  · removed incomplete {name}")

    upload_url = rel["upload_url"].split("{", 1)[0] + f"?name={name}"
    data = Path(path).read_bytes()
    for attempt in range(6):
        try:
            api(upload_url, "POST", data, "application/gzip", timeout=1800)
            print(f"  ✓ uploaded {name} ({size / 1e6:.0f} MB)")
            return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise RuntimeError(f"{name}: HTTP {exc.code} {exc.read().decode(errors='replace')[:200]}")
            err = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            err = exc
        wait = min(2 ** attempt, 30)
        print(f"  ! {name} upload failed ({err}); retry {attempt + 1}/6 in {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {name}")


def main():
    missing = [f for f in FILES if not os.path.exists(f)]
    if missing:
        sys.exit(f"missing files: {missing}")
    rel = get_or_create_release()
    for path in FILES:
        _, rel = api(f"{API}/repos/{REPO}/releases/{rel['id']}")  # refresh asset list
        upload(rel, path)
    print(f"\nDONE — all 3 assets are on release '{TAG}'. Run the Action now. 🎉")


if __name__ == "__main__":
    main()
