-- ════════════════════════════════════════════════════════════════════════════
--  PROMOTIONS
--
--  Modelled directly on the real price-transparency promo feed. Verified field
--  names from promo_file_<chain>.csv:
--    promotionid · promotiondescription · rewardtype · discounttype ·
--    discountrate · allowmultiplediscounts · minqty · maxqty ·
--    discountedprice · discountedpricepermida · minnoofitemofered ·
--    minpurchaseamnt · promotionstart/enddate + hour · promotionitems (JSON)
--
--  Design rule: STORE THE RULE, NOT THE SENTENCE.
--  'שוקולד בלונדי עלית-ב9.90' is kept for display only. The engine reads
--  reward_kind + numeric parameters, so pricing never depends on parsing Hebrew.
-- ════════════════════════════════════════════════════════════════════════════

USE zolt;

-- ── Promotion header: one row per promotion at a branch, time-bound ─────────
CREATE TABLE IF NOT EXISTS promotions (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  chain_id           VARCHAR(20)     NOT NULL,
  store_id           INT UNSIGNED    NOT NULL,
  promo_id_src       VARCHAR(64)     NOT NULL      COMMENT 'promotionid from the feed',

  description        VARCHAR(255)    NULL          COMMENT 'display only — never parsed',

  -- Normalized rule ---------------------------------------------------------
  reward_kind        VARCHAR(20)     NOT NULL DEFAULT 'UNKNOWN'
                     COMMENT 'FIXED_PRICE | PCT_OFF | AMOUNT_OFF | NTH_FREE | BUNDLE_PRICE | UNKNOWN',
  discount_rate      DECIMAL(6,3)    NULL          COMMENT 'fraction, 0.30 = 30% off',
  discount_amount    DECIMAL(10,2)   NULL          COMMENT 'flat ILS off',
  discounted_price   DECIMAL(10,2)   NULL          COMMENT 'price for the qualifying bundle',
  min_qty            DECIMAL(10,3)   NOT NULL DEFAULT 1  COMMENT 'decimal: weighted items use kg',
  max_qty            DECIMAL(10,3)   NULL          COMMENT 'NULL/0 in feed = unlimited',
  min_basket_amount  DECIMAL(10,2)   NULL          COMMENT 'minpurchaseamnt — "₪20 off over ₪100"',

  -- CRITICAL for conflict resolution: feed field `allowmultiplediscounts`.
  -- 0 = this promo may not combine with another on the same line.
  allow_stacking     TINYINT(1)      NOT NULL DEFAULT 0,

  -- NOT a simple id: chains ship a membership EXPRESSION here, e.g. Rami Levy's
  -- '((1=מועדון שיווק השקמה|1=מועדון לקוחות אשראי))&(3=תווי זיכוי מימוש|...)'
  -- (105 chars observed). Stored verbatim; NULL/empty = open to everyone.
  club_id            VARCHAR(255)    NULL          COMMENT 'club restriction expression; NULL = open to all',

  -- Raw source codes preserved so a mis-mapping is fixable without re-ingest.
  reward_type_src    VARCHAR(10)     NULL,
  discount_type_src  VARCHAR(10)     NULL,

  -- Validity: feed splits date and hour into separate columns; the ETL combines
  -- them into real DATETIMEs so the API can filter with a single comparison.
  starts_at          DATETIME        NULL,
  ends_at            DATETIME        NULL,

  updated_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  -- The idempotent upsert target — a re-run updates in place, never duplicates.
  UNIQUE KEY uq_promo_src (chain_id, store_id, promo_id_src),
  -- The hot query: "every promotion active RIGHT NOW at these branches".
  KEY idx_promo_active (store_id, starts_at, ends_at),
  CONSTRAINT fk_promo_store FOREIGN KEY (store_id) REFERENCES stores (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ── Which items a promotion applies to ──────────────────────────────────────
--  Source `promotionitems` is a JSON string, and it has a parsing trap worth
--  naming: a single-item promo serialises as an OBJECT, a multi-item promo as
--  an ARRAY —  {"item": {...}}  vs  {"item": [{...}, {...}]}.
--  The loader must normalize both to a list or it will silently drop items.
CREATE TABLE IF NOT EXISTS promotion_items (
  promotion_id  BIGINT UNSIGNED NOT NULL,
  canonical_id  INT UNSIGNED    NOT NULL   COMMENT 'joins on canonical id, NOT raw barcode',
  is_gift       TINYINT(1)      NOT NULL DEFAULT 0  COMMENT 'the free side of 1+1',
  PRIMARY KEY (promotion_id, canonical_id, is_gift),
  KEY idx_promoitem_canonical (canonical_id),   -- "which promos touch this product?"
  CONSTRAINT fk_promoitem_promo     FOREIGN KEY (promotion_id) REFERENCES promotions (id)         ON DELETE CASCADE,
  CONSTRAINT fk_promoitem_canonical FOREIGN KEY (canonical_id) REFERENCES canonical_products (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Joining on canonical_id (not barcode) is what makes a promotion published
-- against one chain's SKU visible for the same product elsewhere — the whole
-- reason entity resolution comes first.
--
-- Mapping the messy real-world types onto reward_kind:
--   1+1                    → NTH_FREE     min_qty=2,  (free 1)
--   2 for ₪10              → BUNDLE_PRICE min_qty=2,  discounted_price=10
--   'ב-9.90' (fixed price) → FIXED_PRICE  discounted_price=9.90
--   30% off                → PCT_OFF      discount_rate=0.300
--   ₪20 off over ₪100      → AMOUNT_OFF   discount_amount=20, min_basket_amount=100
--   club-members only      → any of the above + club_id
