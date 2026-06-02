"""Download + unzip the Kaggle dataset into the local data dir.

KAGGLE_CONFIG_DIR is pointed at the project's secrets/ folder *before* the
kaggle package is imported, so it finds secrets/kaggle.json automatically.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import DEFAULT_DATA_DIR, PROJECT_ROOT

DEFAULT_CONFIG_DIR = PROJECT_ROOT / "secrets"


def download_dataset(
    dataset: str,
    dest_dir: Path | str = DEFAULT_DATA_DIR,
    config_dir: Path | str = DEFAULT_CONFIG_DIR,
    *,
    quiet: bool = False,
) -> Path:
    """Download `owner/dataset-name` into `dest_dir` (unzipped). Returns dest_dir."""
    config_dir = Path(config_dir)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    token = config_dir / "kaggle.json"
    if not token.exists():
        raise FileNotFoundError(
            f"kaggle.json not found at {token}. Place your Kaggle API token there "
            "(Kaggle → Account → API → Create New Token)."
        )

    # Must be set before importing kaggle (it reads config on authenticate()).
    os.environ["KAGGLE_CONFIG_DIR"] = str(config_dir)

    from kaggle.api.kaggle_api_extended import KaggleApi  # noqa: PLC0415 (lazy import)

    api = KaggleApi()
    api.authenticate()
    api.dataset_download_files(dataset, path=str(dest_dir), unzip=True, quiet=quiet)
    return dest_dir
