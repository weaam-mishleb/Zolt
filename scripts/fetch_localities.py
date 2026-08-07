"""Regenerate etl/localities.json from the CBS locality list on data.gov.il.

WHY THIS EXISTS
---------------
`cities.py` originally recognised 55 cities — the ones that happened to appear in
three chains' files, about 5% of Israel's localities. That is why 540 of 1,860
stores had no city: names like "BE דלית אל כרמל" carry the locality, but there
was nothing to recognise it against.

Committed as data rather than fetched at load time: the ETL must not depend on a
third-party endpoint being up, and a locality list changes about as often as the
municipal map does.

Usage:
    python -m scripts.fetch_localities
"""
from __future__ import annotations

import json
import pathlib
import re
import urllib.parse
import urllib.request

RESOURCE_ID = "5c78e9fa-c2e2-4771-93ff-7f400a12f7ba"   # רשימת ישובים בישראל
API = "https://data.gov.il/api/3/action/datastore_search"
OUT = pathlib.Path(__file__).resolve().parents[1] / "etl" / "localities.json"


def main() -> None:
    url = f"{API}?{urllib.parse.urlencode({'resource_id': RESOURCE_ID, 'limit': 5000})}"
    req = urllib.request.Request(url, headers={"User-Agent": "Zolt/1.0 (city gazetteer)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode("utf-8"))

    out: dict[str, str] = {}
    for rec in payload["result"]["records"]:
        name = re.sub(r"\s+", " ", (rec.get("שם_ישוב") or "").strip())
        code = str(rec.get("סמל_ישוב") or "").strip()
        if name and code.isdigit():
            out[code] = name

    if len(out) < 1000:
        raise SystemExit(f"only {len(out)} localities — refusing to overwrite with a short list")

    OUT.write_text(
        json.dumps(
            {
                "_source": "https://data.gov.il/dataset/citiesandsettelments (CBS רשימת ישובים)",
                "_generated_by": "python -m scripts.fetch_localities",
                "localities": out,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"✅ wrote {OUT.relative_to(OUT.parents[1])} — {len(out)} localities")


if __name__ == "__main__":
    main()
