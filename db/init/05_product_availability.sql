-- ── availability: how many branches carry a product ─────────────────────────
--
-- Denormalised from prices on purpose. Search ranked by
-- COUNT(DISTINCT pr.store_id) over 8.2M price rows, recomputed for every
-- matched product on every keystroke — 1.5s of the 1.5s a query took once the
-- FULLTEXT tombstones were cleared. The value only changes when the ETL runs,
-- so it belongs here and the search never touches `prices` at all.
--
-- No index on it: the FULLTEXT match narrows to a few thousand rows before the
-- sort, so an index would cost write maintenance on every load and buy nothing.
--
-- Guarded rather than `ADD COLUMN IF NOT EXISTS`, which MySQL 8 does not have.
-- The generated statement contains no semicolon, so init_db's splitter keeps it
-- in one piece.
SET @ddl := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE products ADD COLUMN availability INT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''branches carrying this product — maintained by etl.run''',
    'DO 0'
  )
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'products'
    AND COLUMN_NAME  = 'availability'
);
PREPARE _add_availability FROM @ddl;
EXECUTE _add_availability;
DEALLOCATE PREPARE _add_availability;
