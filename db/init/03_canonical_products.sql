-- ════════════════════════════════════════════════════════════════════════════
--  ENTITY RESOLUTION — canonical products
--
--  The problem: chains publish different barcodes/PLUs and different wordings
--  for the same physical item, so matching has to be fuzzy. Today that fuzzy
--  work happens PER REQUEST, on the user's latency budget. At 33 chains and
--  ~250K products that does not hold.
--
--  The fix: resolve identity OFFLINE, in the ETL. Every chain product is mapped
--  once to a canonical product; the online API then does a plain indexed join
--  on canonical_product_id and never tokenizes anything.
--
--  Query-time cost goes from  O(basket × candidates × text work)  →  O(log n).
-- ════════════════════════════════════════════════════════════════════════════

USE zolt;

-- ── One row per real-world item, independent of who sells it ────────────────
CREATE TABLE IF NOT EXISTS canonical_products (
  id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  canonical_name  VARCHAR(255)  NOT NULL              COMMENT 'display name (cleanest variant seen)',
  name_norm       VARCHAR(255)  NOT NULL              COMMENT 'normalized key used for blocking/dedup',
  brand           VARCHAR(120)  NULL,
  net_qty         DECIMAL(12,3) NULL                  COMMENT 'e.g. 55 / 1.5',
  unit            VARCHAR(30)   NULL                  COMMENT 'גרם / ליטר / יחידה',
  size_signature  VARCHAR(40)   NULL                  COMMENT "pack signature, e.g. '4|55' — keeps a 4-pack distinct from a single",
  category        VARCHAR(80)   NULL                  COMMENT 'reserved: not in the source feed today',
  is_weighted     TINYINT(1)    NOT NULL DEFAULT 0,
  member_count    INT UNSIGNED  NOT NULL DEFAULT 0    COMMENT 'how many chain products map here (denormalized for QA)',
  created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  -- Blocking key: normalized name + pack signature. This is what stops
  -- 'ביסלי גריל 55 גר' and 'ביסלי גריל 4*55' from collapsing into one item.
  UNIQUE KEY uq_canon_identity (name_norm, size_signature),
  KEY idx_canon_brand (brand),
  FULLTEXT KEY ft_canon_name (canonical_name)   -- autocomplete only, never for matching
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ── chain product  →  canonical product ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_map (
  product_id     INT UNSIGNED NOT NULL              COMMENT 'FK products.id (a chain-specific SKU)',
  canonical_id   INT UNSIGNED NOT NULL,
  match_method   VARCHAR(24)  NOT NULL              COMMENT 'barcode | exact_name | trigram | manual',
  confidence     DECIMAL(4,3) NOT NULL DEFAULT 1.000 COMMENT '1.000 = certain; low scores go to review',
  needs_review   TINYINT(1)   NOT NULL DEFAULT 0,
  matched_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id),                        -- a SKU maps to exactly one canonical item
  KEY idx_map_canonical (canonical_id),            -- the hot path: canonical → all its SKUs
  KEY idx_map_review (needs_review, confidence),   -- the human review queue
  CONSTRAINT fk_map_product   FOREIGN KEY (product_id)   REFERENCES products (id)           ON DELETE CASCADE,
  CONSTRAINT fk_map_canonical FOREIGN KEY (canonical_id) REFERENCES canonical_products (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Why `confidence` + `needs_review` exist:
-- matching quality stops being a guess and becomes measurable. Low-confidence
-- pairs are queued for a human instead of silently producing a wrong price —
-- the failure mode that costs users real money.
