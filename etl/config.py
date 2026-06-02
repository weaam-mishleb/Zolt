"""ETL configuration: data location, supported chains, batch sizes."""
from __future__ import annotations

from pathlib import Path

# Project root (Zolt/) and the local unzipped Kaggle dataset.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "archive"

# chain slug -> Hebrew display name. The numeric chain_id is read from the
# files themselves; these ids are kept only for sanity logging.
CHAINS: dict[str, str] = {
    "shufersal": "שופרסל",
    "rami_levy": "רמי לוי",
    "osher_ad": "אושר עד",
}
CHAIN_IDS: dict[str, str] = {
    "shufersal": "7290027600007",
    "rami_levy": "7290058140886",
    "osher_ad": "7290103152017",
}

# Upsert batch size (the plan's "groups of 1,000") and streaming chunk size.
BATCH_SIZE = 1000
CHUNK_SIZE = 50_000

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
