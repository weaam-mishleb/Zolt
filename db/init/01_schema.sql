-- ════════════════════════════════════════════════════════════════════
--  Zolt — supermarket basket comparison
--  MySQL 8.0 schema (Shufersal · Rami Levy · Osher Ad)
--
--  Source feed: Israeli "price transparency" CSV/XML format.
--    Chain EAN codes:
--      7290027600007  שופרסל   (Shufersal)
--      7290058140886  רמי לוי   (Rami Levy)
--      7290103152017  אושר עד   (Osher Ad)
-- ════════════════════════════════════════════════════════════════════

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS zolt
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE zolt;

-- ────────────────────────────────────────────────────────────────────
--  stores — one row per physical branch of any supported chain.
--  Natural key from the feed: (chain_id, sub_chain_id, store_code).
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stores (
  id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  chain_id      VARCHAR(20)   NOT NULL                COMMENT 'Chain EAN code, e.g. 7290027600007 (Shufersal)',
  chain_name    VARCHAR(100)  NOT NULL                COMMENT 'Human chain name: שופרסל / רמי לוי / אושר עד',
  sub_chain_id  VARCHAR(20)   NOT NULL DEFAULT ''     COMMENT 'subchainid from feed',
  store_code    VARCHAR(20)   NOT NULL                COMMENT 'storeid within the chain',
  store_name    VARCHAR(200)  NULL                    COMMENT 'storename',
  address       VARCHAR(255)  NULL,
  city          VARCHAR(100)  NULL                    COMMENT 'used by /stores and basket comparison filters',
  zip_code      VARCHAR(20)   NULL,
  created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_store_natural (chain_id, sub_chain_id, store_code),
  KEY idx_store_city  (city),
  KEY idx_store_chain (chain_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ────────────────────────────────────────────────────────────────────
--  products — one row per item, keyed by its barcode (itemcode).
--  FULLTEXT on name powers text search & autocomplete (Step 2).
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
  id               INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  barcode          VARCHAR(50)   NOT NULL              COMMENT 'itemcode — unique product barcode',
  name             VARCHAR(255)  NOT NULL              COMMENT 'itemname',
  manufacturer     VARCHAR(255)  NULL                  COMMENT 'manufacturename',
  unit_qty         VARCHAR(50)   NULL                  COMMENT 'unitqty (e.g. גרם / מיליליטר)',
  quantity         DECIMAL(12,3) NULL                  COMMENT 'quantity value',
  unit_of_measure  VARCHAR(50)   NULL                  COMMENT 'unitofmeasure',
  is_weighted      TINYINT(1)    NOT NULL DEFAULT 0    COMMENT 'bisweighted',
  created_at       TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_product_barcode (barcode),
  FULLTEXT KEY ft_product_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ────────────────────────────────────────────────────────────────────
--  prices — current price of a product at a specific store.
--  CRITICAL: UNIQUE(product_id, store_id) is the upsert target used by
--  the ETL's `INSERT ... ON DUPLICATE KEY UPDATE` (Step 3).
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prices (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id         INT UNSIGNED    NOT NULL,
  store_id           INT UNSIGNED    NOT NULL,
  price              DECIMAL(10,2)   NOT NULL          COMMENT 'itemprice',
  unit_price         DECIMAL(10,2)   NULL              COMMENT 'unitofmeasureprice',
  allow_discount     TINYINT(1)      NOT NULL DEFAULT 1 COMMENT 'allowdiscount',
  item_status        VARCHAR(20)     NULL              COMMENT 'itemstatus',
  price_update_time  DATETIME        NULL              COMMENT 'priceupdatetime from feed',
  created_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_price_product_store (product_id, store_id),
  KEY idx_price_store (store_id),
  CONSTRAINT fk_price_product
    FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_price_store
    FOREIGN KEY (store_id)   REFERENCES stores   (id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
