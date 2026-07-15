-- ════════════════════════════════════════════════════════════════════
--  etl_jobs — one row per ETL run, updated live while the run streams.
--
--  The ETL (running on a GitHub Actions runner) writes its progress here,
--  and the FastAPI backend reads the latest row for the admin panel's
--  progress bar — the shared MySQL DB is the channel between them, so no
--  direct runner ↔ backend connection is needed.
-- ════════════════════════════════════════════════════════════════════

USE zolt;

CREATE TABLE IF NOT EXISTS etl_jobs (
  id           BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  source       VARCHAR(20)      NOT NULL DEFAULT 'cli'    COMMENT 'github (admin dispatch) / cli (workflow or shell) / local',
  status       VARCHAR(20)      NOT NULL DEFAULT 'queued' COMMENT 'queued / running / success / failed',
  progress     TINYINT UNSIGNED NOT NULL DEFAULT 0        COMMENT '0–100 percent',
  phase        VARCHAR(120)     NULL                      COMMENT 'human-readable current step',
  is_full      TINYINT(1)       NOT NULL DEFAULT 0        COMMENT 'full catalog vs snapshot',
  detail       TEXT             NULL                      COMMENT 'summary on success / error on failure',
  started_at   DATETIME         NULL,
  finished_at  DATETIME         NULL,
  created_at   TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_etl_jobs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
