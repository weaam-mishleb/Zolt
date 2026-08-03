"""etl/chains.json is the single source of truth for which chains we load.

The GitHub Actions matrix and the Python loaders both read it, so a mistake here
either silently drops a chain from the nightly run or makes `--chains <slug>`
reject a slug the matrix happily dispatches.
"""
from __future__ import annotations

import json
import pathlib

from etl.config import CHAINS, _CHAINS_FILE, _load_chains, price_file, promo_file, store_file


def test_chains_json_exists_and_parses():
    data = json.loads(_CHAINS_FILE.read_text(encoding="utf-8"))
    assert isinstance(data.get("chains"), list) and data["chains"]


def test_every_entry_has_a_slug_and_a_name():
    for c in json.loads(_CHAINS_FILE.read_text(encoding="utf-8"))["chains"]:
        assert c.get("slug"), f"entry without slug: {c}"
        assert c.get("name"), f"{c['slug']} has no display name"


def test_slugs_are_unique():
    slugs = [c["slug"] for c in json.loads(_CHAINS_FILE.read_text(encoding="utf-8"))["chains"]]
    assert len(slugs) == len(set(slugs)), "duplicate slug — the matrix would run it twice"


def test_config_loads_every_enabled_chain():
    """Regression: CHAINS used to be a hard-coded dict of 3, so `--chains
    bareket` failed with 'invalid choice' while the matrix dispatched it."""
    assert len(CHAINS) >= 30
    for slug in ("shufersal", "rami_levy", "osher_ad", "bareket", "yohananof"):
        assert slug in CHAINS


def test_every_disabled_chain_says_why():
    """A chain parked without a reason gets re-enabled by the next person and
    fails the nightly run again. Three are currently off because the upstream
    scraper publishes nothing usable for them, not because of anything we do."""
    for c in json.loads(_CHAINS_FILE.read_text(encoding="utf-8"))["chains"]:
        if c.get("enabled", True) is False:
            assert c.get("reason"), f"{c['slug']} is disabled with no reason"


def test_disabled_chains_are_excluded(tmp_path, monkeypatch):
    cfg = tmp_path / "chains.json"
    cfg.write_text(json.dumps({"chains": [
        {"slug": "on_chain", "name": "פעילה", "enabled": True},
        {"slug": "off_chain", "name": "מושבתת", "enabled": False},
        {"slug": "default_on", "name": "ברירת מחדל"},        # enabled omitted
    ]}), encoding="utf-8")
    monkeypatch.setattr("etl.config._CHAINS_FILE", cfg)
    loaded = _load_chains()
    assert set(loaded) == {"on_chain", "default_on"}


def test_missing_or_broken_file_falls_back_instead_of_crashing(tmp_path, monkeypatch):
    """A bad config must not make `import etl.config` explode mid-pipeline."""
    monkeypatch.setattr("etl.config._CHAINS_FILE", pathlib.Path("/nonexistent/chains.json"))
    assert set(_load_chains()) == {"shufersal", "rami_levy", "osher_ad"}

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("etl.config._CHAINS_FILE", broken)
    assert set(_load_chains()) == {"shufersal", "rami_levy", "osher_ad"}


def test_file_helpers_match_the_names_the_workflow_downloads():
    """The matrix asks Kaggle for exactly these filenames — they must agree."""
    d = pathlib.Path("archive")
    assert price_file(d, "bareket", full=True).name == "price_full_file_bareket.csv"
    assert price_file(d, "bareket", full=False).name == "price_file_bareket.csv"
    assert store_file(d, "bareket").name == "store_file_bareket.csv"
    assert promo_file(d, "bareket", full=False).name == "promo_file_bareket.csv"
    assert promo_file(d, "bareket", full=True).name == "promo_full_file_bareket.csv"
