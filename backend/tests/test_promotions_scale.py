"""Static guards for the full-catalogue promotion path."""
from __future__ import annotations

from pathlib import Path

from scripts.init_db import _statements


ROOT = Path(__file__).parents[2]


def test_promotions_use_a_dedicated_5000_row_write_batch():
    from etl.config import BATCH_SIZE, PROMOTION_WRITE_BATCH_SIZE

    assert PROMOTION_WRITE_BATCH_SIZE == 5_000
    # Do not enlarge every writer just to speed up promotion links.
    assert BATCH_SIZE < PROMOTION_WRITE_BATCH_SIZE


def test_nightly_workflow_loads_the_full_promotion_catalogue():
    workflow = (ROOT / ".github" / "workflows" / "etl-matrix.yml").read_text("utf-8")
    assert 'python -m etl.promotions --chains "$CHAIN" --full' in workflow


def test_promotion_index_migration_is_idempotent_and_non_redundant():
    sql = (ROOT / "db" / "init" / "06_promotion_read_indexes.sql").read_text("utf-8")
    statements = _statements(sql)

    assert "information_schema.statistics" in sql
    assert "idx_promo_active" in sql
    assert "idx_promoitem_canonical" in sql
    assert any(s.startswith("PREPARE zolt_idx_stmt") for s in statements)
    assert any(s.startswith("EXECUTE zolt_idx_stmt") for s in statements)

    # InnoDB puts promotion_id (the PK prefix) in the canonical secondary
    # B-Tree already. An explicit duplicate would double write amplification.
    executable = "\n".join(statements)
    assert "(canonical_id, promotion_id)" not in executable
    assert "CREATE INDEX idx_promoitem_promotion" not in executable
