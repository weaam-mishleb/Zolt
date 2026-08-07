-- Promotion read indexes, idempotent for databases created before
-- db/init/04_promotions.sql gained its keys.
--
-- Hot API query:
--   promotion_items canonical_id -> promotion_id -> promotions store/date
--
-- InnoDB automatically appends the PRIMARY KEY columns to every secondary
-- index. Therefore idx_promoitem_canonical(canonical_id) physically carries
-- promotion_id already; creating an explicit (canonical_id, promotion_id)
-- index would duplicate the same B-Tree and make the full ingest pay twice.
-- Likewise promotion_items' PRIMARY KEY begins with promotion_id, and
-- idx_promo_active begins with store_id. No standalone duplicates are needed.

SET @has_promo_active = (
  SELECT COUNT(*)
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'promotions'
    AND index_name = 'idx_promo_active'
);
SET @ddl = IF(
  @has_promo_active = 0,
  'CREATE INDEX idx_promo_active ON promotions (store_id, starts_at, ends_at)',
  'SELECT 1'
);
PREPARE zolt_idx_stmt FROM @ddl;
EXECUTE zolt_idx_stmt;
DEALLOCATE PREPARE zolt_idx_stmt;

SET @has_promoitem_canonical = (
  SELECT COUNT(*)
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'promotion_items'
    AND index_name = 'idx_promoitem_canonical'
);
SET @ddl = IF(
  @has_promoitem_canonical = 0,
  'CREATE INDEX idx_promoitem_canonical ON promotion_items (canonical_id)',
  'SELECT 1'
);
PREPARE zolt_idx_stmt FROM @ddl;
EXECUTE zolt_idx_stmt;
DEALLOCATE PREPARE zolt_idx_stmt;
