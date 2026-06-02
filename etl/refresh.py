"""End-to-end refresh: download the latest Kaggle dataset, then run the ETL.

This is what the weekly scheduler (and the admin manual-trigger) invokes.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import DEFAULT_DATA_DIR
from .downloaders.kaggle_download import DEFAULT_CONFIG_DIR, download_dataset
from .run import run_pipeline

log = logging.getLogger("zolt.etl.refresh")


def refresh_from_kaggle(
    dataset: str,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    config_dir: Path | str = DEFAULT_CONFIG_DIR,
    *,
    full: bool = False,
) -> dict:
    """Download `dataset` into `data_dir`, then run the ETL upserts.

    Returns a small summary dict (also useful for logging / the admin endpoint).
    """
    t0 = time.time()
    log.info("refresh: downloading %s → %s", dataset, data_dir)
    download_dataset(dataset, dest_dir=data_dir, config_dir=config_dir)

    log.info("refresh: running ETL (full=%s)", full)
    run_pipeline(data_dir=data_dir, full=full)

    elapsed = round(time.time() - t0, 1)
    log.info("refresh: done in %ss", elapsed)
    return {"dataset": dataset, "data_dir": str(data_dir), "full": full, "elapsed_s": elapsed}
