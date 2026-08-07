"""Measure how many of our products Open Food Facts actually has an image for.

WHY THIS EXISTS
---------------
Before building a UI around product images, the question that decides the design
is coverage. If OFF knows 15% of the catalogue then images are not the feature —
the placeholder is, and the layout has to be built around absence rather than
treating it as an edge case.

Only real GTINs are sampled. Roughly a tenth of our barcodes are internal chain
codes namespaced as "<chain_id>_<code>" (see etl.normalize.product_key); OFF
cannot possibly know those, and including them would understate real coverage.

Deliberately polite: sequential, with a delay between calls. OFF is a volunteer-
run nonprofit and their product API asks for well under 100 requests/minute.

Usage:
    python -m scripts.off_coverage --sample 200
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter

from sqlalchemy import text

API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
FIELDS = "code,product_name,brands,image_url,image_front_url"
# A contact address is what their terms ask of any automated caller.
UA = "Zolt/1.0 (grocery price comparison; coverage survey; weaam_m19@icloud.com)"
DELAY_S = 0.7


def fetch(barcode: str, timeout: int = 15) -> dict | None:
    req = urllib.request.Request(
        f"{API.format(barcode=barcode)}?fields={FIELDS}", headers={"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"status": 0, "_http": e.code}
    except Exception:                        # noqa: BLE001 — a survey must not die on one row
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Open Food Facts image-coverage survey")
    p.add_argument("--sample", type=int, default=200)
    args = p.parse_args()

    from backend.app.db import engine

    with engine.connect() as c:
        rows = c.execute(
            text(
                # Real GTINs only, and weight the sample toward products people
                # actually see: a barcode in one branch is not what the UI shows.
                "SELECT barcode, name, availability FROM products "
                "WHERE barcode NOT LIKE '%\\_%' AND CHAR_LENGTH(barcode) BETWEEN 8 AND 14 "
                "AND availability > 0 ORDER BY RAND() LIMIT :n"
            ),
            {"n": args.sample},
        ).all()

    print(f"sampled {len(rows)} real-GTIN products; querying Open Food Facts…\n")
    stat = Counter()
    examples: list[tuple[str, str, str]] = []
    t0 = time.time()

    for i, (barcode, name, avail) in enumerate(rows, 1):
        data = fetch(barcode)
        if data is None:
            stat["network error"] += 1
        elif data.get("status") == 1:
            prod = data.get("product") or {}
            img = prod.get("image_front_url") or prod.get("image_url")
            if img:
                stat["found WITH image"] += 1
                if len(examples) < 5:
                    examples.append((barcode, name[:34], prod.get("product_name") or ""))
            else:
                stat["found, NO image"] += 1
        else:
            stat["not in OFF"] += 1
        if i % 25 == 0:
            print(f"  … {i}/{len(rows)}", flush=True)
        time.sleep(DELAY_S)

    n = len(rows)
    print(f"\n── coverage over {n} products ({time.time() - t0:.0f}s) ──")
    for k, v in stat.most_common():
        print(f"  {k:22} {v:>4}  {100 * v / n:5.1f}%")
    usable = stat["found WITH image"]
    print(f"\n  USABLE IMAGE COVERAGE: {100 * usable / n:.1f}%")
    if examples:
        print("\n  matches (our name → OFF name):")
        for bc, ours, theirs in examples:
            print(f"    {bc}  {ours}  →  {theirs[:34]}")


if __name__ == "__main__":
    main()
