"""ETL configuration: data location, supported chains, batch sizes."""
from __future__ import annotations

import json
from pathlib import Path

# Project root (Zolt/) and the local unzipped Kaggle dataset.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "archive"

# chain slug -> Hebrew display name, loaded from etl/chains.json so the file is
# the single source of truth: the GitHub Actions matrix and the loaders read the
# same list, and adding a chain never means editing Python.
#
# The numeric chain_id is read from the feed files themselves; the ids below are
# kept only for sanity logging.
_CHAINS_FILE = Path(__file__).with_name("chains.json")


def _load_chains() -> dict[str, str]:
    try:
        data = json.loads(_CHAINS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Never let a missing or malformed config file break an import — fall
        # back to the three chains the project started with.
        return {"shufersal": "שופרסל", "rami_levy": "רמי לוי", "osher_ad": "אושר עד"}
    return {
        c["slug"]: c.get("name", c["slug"])
        for c in data.get("chains", [])
        if c.get("slug") and c.get("enabled", True)
    }


CHAINS: dict[str, str] = _load_chains()
CHAIN_IDS: dict[str, str] = {
    "shufersal": "7290027600007",
    "rami_levy": "7290058140886",
    "osher_ad": "7290103152017",
}

# Upsert batch size and streaming chunk size. Batch size is small (250) so each
# multi-row INSERT clears a high-latency WAN / cloud proxy quickly; for a very
# distant or flaky DB drop it further with `--batch-size 100`.
# Chunk size is kept modest so peak RAM stays low (stdlib csv streaming).
BATCH_SIZE = 250
CHUNK_SIZE = 5_000

# The feed is "grouped": only the first row of each store block carries the
# chain/store identity columns; the rest are blank. These columns are
# forward-filled (carried down) before normalization. Per-product columns
# (itemcode, itemname, itemprice, ...) are intentionally NOT in this set.
GROUP_FILL_COLS = (
    "found_folder",
    "file_name",
    "chainid",
    "chainname",
    "subchainid",
    "subchainname",
    "storeid",
    "bikoretno",
    "lastupdatedate",
    "lastupdatetime",
)


def store_file(data_dir: Path, slug: str) -> Path:
    return data_dir / f"store_file_{slug}.csv"


def price_file(data_dir: Path, slug: str, full: bool = False) -> Path:
    name = f"price_full_file_{slug}.csv" if full else f"price_file_{slug}.csv"
    return data_dir / name


def promo_file(data_dir: Path, slug: str, full: bool = False) -> Path:
    name = f"promo_full_file_{slug}.csv" if full else f"promo_file_{slug}.csv"
    return data_dir / name
