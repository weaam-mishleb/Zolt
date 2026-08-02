# Zolt — Nationwide Scale-Up & Promotions: Technical Plan

**Scope change:** from 3 chains / 543 branches / prices-only → **all reporting chains
nationwide + promotions + price history**.

Every number below is **measured** on the current production system and the actual
Kaggle dataset, not assumed. Where something is a projection, it says so.

---

## 0. The measured baseline

| Metric | Today (production) | Target (measured in the dataset) | Factor |
|---|---|---|---|
| Chains | 3 | **33** (`price_full` files present) | ×11 |
| Branches | 543 | **2,489** (store-file rows) | ×4.6 |
| Price rows | 1,982,479 | **14,099,004** | ×7.1 |
| Promotion rows | 0 | **4,341,963** | new |
| Distinct products | 49,995 | ~200–300K (projected) | ×4–6 |
| DB size | **237.5 MB** | see §1 | — |

Per-row cost **measured in InnoDB** (data + index, current schema):

| Table | Bytes/row | Note |
|---|---|---|
| `prices` | **123 B** | 110.7 MB data + 108.3 MB index over 1.98M rows |
| `products` | **383 B** | wide text + FULLTEXT + `name_norm` index |
| `stores` | **302 B** | negligible at any scale |

Raw source volume: **2.2 GB** price CSVs + **8.6 GB** promotion CSVs. Note the
inversion — promotions are only 31% of the rows but **~4× the bytes**, because each
promo row carries description text and repeated item/condition fields. Promotions are
a *text-heavy*, not a *row-heavy*, problem.

---

## 1. Capacity & Resource Planning

### 1.1 Storage — live ("current state") tier

Using the measured per-row costs:

| Table | Rows | × B/row | Size |
|---|---|---|---|
| `prices` | 14.1M | 123 B | **1.73 GB** |
| `promotions` + `promotion_items` | 4.34M | ~300 B (projected, wider rows) | **1.30 GB** |
| `products` | ~250K | 383 B | **96 MB** |
| `stores` | 2,489 | 302 B | 0.8 MB |
| **Live tier total** | | | **≈ 3.2 GB** |

**Verdict: the live tier is small.** 3.2 GB is comfortably a single managed Postgres/MySQL
instance. Nationwide scale does **not** by itself justify leaving a relational DB.

### 1.2 Storage — history tier (this is the real decision)

History is where naive design explodes. Three options, same data:

| Strategy | Daily | Per year | Viable? |
|---|---|---|---|
| Full daily snapshot in InnoDB | 1.73 GB | **632 GB** | ❌ no |
| Delta-only rows (~10% daily churn) | ~173 MB | **~63 GB** | ⚠️ workable, heavy |
| Delta + **columnar compressed** (ClickHouse / Parquet), 10–15× | ~15 MB | **≈ 4–6 GB** | ✅ recommended |

Two rules make this work:

1. **Only write what changed.** The ETL already does idempotent upserts; add a
   `price_history` append that fires only when `price` actually differs from the stored
   value. Retail prices are sticky — most rows don't change day to day.
2. **History never lives in the OLTP database.** Append it to a columnar store
   (ClickHouse) or partitioned Parquet on object storage. Row-store + B-tree indexes are
   the worst possible format for append-only time series.

> Assumption flagged: the ~10% daily churn figure is an industry-typical estimate, **not
> measured** — we have a single snapshot, not a time series. Instrument it in week 1 by
> diffing two consecutive loads before committing to a retention budget.

### 1.3 Compute & memory — ETL

The OOM risk you hit at 512 MB is **already architecturally solved**: the loader streams
row-by-row, so peak memory is O(chunk), not O(file). Measured: **89 MB peak** on an 85 MB
file / 489K rows. That property holds at 33 chains **as long as the streaming discipline
is preserved per file**.

| Resource | Recommendation | Why |
|---|---|---|
| RAM | **2 GB** (≈89 MB measured + headroom) | Promo parsing is nested/wider; leave room for batch buffers |
| vCPU | **2–4** | Parse + normalize is CPU-light; run one worker per chain |
| Wall time | ~25 min single-threaded → **~8–10 min** at 4-way per-chain parallelism | 18.4M rows at the measured ~13K rows/s |
| Disk | 20 GB ephemeral | 14 GB raw + gunzip headroom |

**The dominant cost is not RAM — it is network distance to the DB.** Measured on this very
project: the same ETL ran in **29.6 s against a local DB** and **timed out past 600 s over
the WAN**. At 7× the rows that gap is fatal.

> **Action: co-locate the ETL runner and the database in the same region/VPC.** This single
> change matters more than any hardware upgrade. GitHub-hosted runners are in US regions
> and the DB is not — that pairing does not survive this scale.

### 1.4 Compute & memory — Backend API

| Resource | Recommendation | Why |
|---|---|---|
| RAM | **2–4 GB** per instance | FastAPI itself is light; headroom for connection pool + cache |
| vCPU | **2** per instance, **≥2 instances** behind a load balancer | The app is stateless — horizontal scaling is trivial and cheap |
| DB | **4–8 vCPU, 16 GB RAM** | Buffer pool ≈70% of RAM (≈11 GB) holds the entire 3.2 GB live tier **and** its indexes in memory — this is what keeps latency flat |
| Cache | **Redis, 1–2 GB** | City/basket comparison results, autocomplete head queries |

Sizing the DB so the working set fits in the buffer pool is the highest-leverage decision
here: it turns almost every read into a memory hit.

---

## 2. Architectural Upgrades

### 2.1 Should we move to NoSQL or a Data Warehouse?

**No to NoSQL. Yes to a warehouse — but only for history.**

The data is aggressively relational: products × stores × prices × promotions × promotion
items, queried with joins, aggregates and ranking. That is exactly what a relational
engine is for. A document store would force you to either duplicate prices inside store
documents (write amplification, update anomalies) or reimplement joins in application
code. **NoSQL solves a problem you do not have.**

The right split is by *workload*, not by *volume*:

```
┌──────────────────────────────────────────────────────────────┐
│  OLTP / serving tier — MySQL or Postgres (~3.2 GB)           │
│  current prices · current promotions · products · stores     │
│  → user-facing reads, always "now", indexed, sub-100ms       │
└──────────────────────────────────────────────────────────────┘
                     │ append changed rows only
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  OLAP / history tier — ClickHouse or Parquet + DuckDB        │
│  price_history · promo_history (compressed, partitioned)     │
│  → trends, "was it cheaper last month", analytics            │
└──────────────────────────────────────────────────────────────┘
```

**MySQL → Postgres?** Optional, and *not* required by scale. The honest trade-off:

| | Stay on MySQL 8 | Move to Postgres 16 |
|---|---|---|
| For | Zero migration risk; the schema, `name_norm` generated column and FULLTEXT already work | `pg_trgm` + GIN for real fuzzy matching, far better partitioning, richer indexing |
| Against | Weaker text search, clumsier partitioning | A migration you must plan, test and cut over |

Recommendation: **stay on MySQL** if you adopt §2.3 (matching moves out of the query
path), because then you need far less from the text engine. Migrate to Postgres only if
you decide fuzzy matching must stay in the database.

### 2.2 Airflow? Spark?

**Spark: no.** 18.4M rows totalling ~11 GB is *small data*. Spark's cluster and shuffle
overhead would exceed the actual work; single-node streaming Python (or DuckDB for
transforms) is both faster and dramatically simpler here. Reach for Spark at ~100M+ rows
or genuinely distributed joins — neither applies.

**Airflow: not yet.** Airflow needs its own metadata DB, scheduler and workers — that is
real operational weight for a pipeline that is currently one linear job.

What you actually need is *orchestration features*, which you can get in stages:

| Stage | Tool | When |
|---|---|---|
| **Now** | GitHub Actions, restructured: **one job per chain** in a matrix, with retries and per-chain status in `etl_jobs` | 33 chains × ~1 min each fits the free tier; a single chain failing stops being an all-or-nothing failure |
| **Next** | **Prefect** or **Dagster** (managed/serverless) | When you need backfills, dependencies, data lineage and real alerting |
| **Later** | Airflow | Only with many interdependent DAGs and a team to operate it |

The matrix restructure alone buys most of the value: isolation, parallelism (§1.3), and
per-chain retry — for a few lines of YAML rather than a new platform.

### 2.3 Text matching at scale — the most important change

**Today, fuzzy matching happens per request.** For every basket line the API runs
tokenization → `MATCH…AGAINST` → size/head filtering, against a growing product table.
That is `O(basket × candidates)` work on the *user's* latency budget, and at 250K
products across 33 chains, it degrades on two axes at once: more candidates, and more
near-duplicate names to disambiguate.

**Fix: move matching from query time to ETL time (entity resolution).**

```
ETL (offline, nightly)                      API (online, per request)
─────────────────────                       ────────────────────────
raw product rows                            basket line
   → blocking (barcode / brand+size key)       → canonical_product_id  (already known)
   → similarity scoring (trigram/embedding)    → indexed join on prices
   → canonical_product_id assigned             → sub-millisecond
   → stored in product_map
```

Add one table:

```sql
canonical_products (id, canonical_name, brand, net_qty, unit, category)
product_map (chain_product_id → canonical_id, match_method, confidence)
```

Consequences:
- **Query time becomes a plain indexed join.** No FULLTEXT, no tokenization, no
  per-request scanning. This is how production price-comparison systems work.
- Matching quality becomes **reviewable and improvable offline** — you can measure it,
  A/B it, and hand low-confidence pairs to a human queue, instead of guessing live.
- Expensive techniques become affordable: trigram similarity, embeddings, brand
  dictionaries — all fine in a nightly batch, impossible in a 100 ms request.

**Autocomplete** (genuinely free-text, user-facing) moves to a dedicated search index —
**Typesense** or **Meilisearch** (simple, Hebrew-friendly, low ops) or **OpenSearch** (more
power, more ops) — over ~250K canonical products. Typical latency 10–30 ms, with typo
tolerance and prefix search that MySQL FULLTEXT cannot match.

---

## 3. Data Modeling & Promotions Logic

### 3.1 Why promotions break the current model

Today's model assumes `line_total = price × quantity`. Promotions destroy that assumption:
the price of an item depends on **how many** you buy, **what else** is in the cart, **when**
you shop, and sometimes **who** you are (club member). Cost stops being a per-line
property and becomes a **property of the basket at a store at a moment**.

### 3.2 Schema

```sql
-- header: one row per promotion at a store, time-bound
promotions (
  id, chain_id, store_id, promo_id_src,       -- source id, for idempotent upsert
  description, reward_type, discount_rate, discount_amount,
  min_qty, min_basket_amount, max_qty,
  club_id,                                     -- NULL = open to all
  starts_at, ends_at,
  UNIQUE (chain_id, store_id, promo_id_src),   -- the upsert target
  INDEX (store_id, starts_at, ends_at)         -- "active now at this store"
)

-- which products participate
promotion_items (promotion_id, canonical_product_id, is_gift,
                 INDEX (canonical_product_id), INDEX (promotion_id))
```

Design notes:
- **Model the rule, not the sentence.** `reward_type` ∈ {`PCT_OFF`, `FIXED_TOTAL`,
  `NTH_FREE`, `BUNDLE_PRICE`, `AMOUNT_OFF`, …} with numeric parameters. The Hebrew
  description is kept for display only — never parsed at query time.
- **Time-bound by design.** Every promo carries `starts_at`/`ends_at`; queries always
  filter `NOW() BETWEEN starts_at AND ends_at`. Nothing is "current" implicitly.
- **Join on `canonical_product_id`, not raw barcodes** — otherwise a promo attached to one
  chain's barcode is invisible to the same product from another chain (§2.3).
- **Weight-based items** (`is_weighted`) use `quantity` as a decimal in kg and
  `min_qty` as a weight threshold — the same rule engine covers them.

Mapping the messy real types:

| Real promo | Model |
|---|---|
| 1+1 | `NTH_FREE`, min_qty=2, free_count=1 |
| 2 for ₪10 | `BUNDLE_PRICE`, min_qty=2, discount_amount=10 |
| 30% off | `PCT_OFF`, discount_rate=0.30 |
| 3rd item 50% off | `NTH_FREE`-family, nth=3, discount_rate=0.50 |
| ₪20 off over ₪100 | `AMOUNT_OFF`, min_basket_amount=100 |
| Club members only | any of the above + `club_id` set |

### 3.3 The cheapest-cart algorithm with promotions

This stops being a sum and becomes a small **optimization problem** — formally a
set-selection problem with conflicts. Keep it tractable by exploiting the real bounds:
a basket is ≤50 lines and a store has a limited number of active promos.

Per candidate store:

```
1. Resolve   — basket lines → canonical_product_ids (done at ETL time, free here)
2. Base cost — Σ(unit_price × qty)                     ← today's logic, unchanged
3. Fetch     — promotions active NOW at this store touching these canonical ids
               (one indexed query per store, or one batched query for all stores)
4. Evaluate  — for each promo, compute achievable discount given basket quantities
5. Resolve conflicts — promos that cannot stack compete for the same lines:
               • default: greedy by discount value (near-optimal, O(n log n))
               • exact: DP/branch-and-bound when a line has ≤k competing promos
6. Final     — base_cost − Σ(applied discounts), plus a per-line breakdown
7. Rank stores by final cost (complete stores only — the existing rule holds)
```

Two engineering guardrails:

- **The promo engine must be a pure function** — `(basket, prices, promos, now) → priced
  basket`. No DB access inside. That keeps it unit-testable exactly like
  `build_comparison` is today, which is what let this project test ranking without a
  database at all. Every promo type gets its own test case.
- **Show the math.** The UI already explains *why* a store wins; with promotions that
  becomes essential — "₪124.50, includes ₪18 from 3 promotions" with the breakdown, or
  users will not trust the result.

**Correctness caveat worth stating up front:** greedy conflict resolution is not
guaranteed optimal. For a price-comparison product this is an acceptable, standard
trade-off — but it must be a *conscious* one, and the exact solver should be used when
the conflict set is small.

---

## 4. Maintenance, Monitoring & Debugging

The existing `etl_jobs` table is the right foundation — it already gives live progress,
status and stale-run detection. Scale it out along four axes:

### 4.1 Observability

| Layer | What | Tool |
|---|---|---|
| Pipeline | Per-**chain** run rows: rows read/skipped/upserted, duration, peak RSS | extend `etl_jobs` with `chain` + counters |
| Data quality | Assertions per run (see §4.2) | fail the chain, not the pipeline |
| App | Structured JSON logs with request id; p50/p95 latency; error rate | Grafana Cloud / Better Stack free tiers |
| Alerting | Chain failed · chain skipped 2 days · DQ assertion breached | GitHub Actions failure email + webhook |

**Structured logging is the biggest single gap today.** Most of this project's production
incidents were diagnosed blind — that does not scale to 33 daily pipelines.

### 4.2 Data-quality gates (the part that saves you)

Run these **after load, before promoting the data as current**. Any breach fails that
chain and keeps yesterday's data live — a stale price beats a wrong price:

- Row count within ±30% of that chain's trailing 7-day average
- Store count for the chain did not drop
- Price sanity: no negative/zero prices; ≤0.1% of prices outside the item's historical
  p1–p99 band *(this exact class of bug — an absurd price silently winning a comparison —
  is currently unguarded)*
- Promotions: `ends_at > starts_at`; ≤X% expired-on-arrival
- Freshness: `MAX(price_update_time)` is within 48 h

### 4.3 Debuggability

- **Keep raw files immutable and dated** in object storage (`s3://…/2026-08-02/shufersal/`).
  Every load must be reproducible from its exact input — this is what makes "why did this
  price change?" answerable at all.
- **Per-chain isolation** (the §2.2 matrix): one chain's malformed file cannot take down
  the other 32.
- **Keep the local Docker path working.** The ability to run the full pipeline against a
  local MySQL in ~30 s is this project's most valuable debugging asset — it is what
  turned the production "zombie instance" incident from guesswork into a reproducible
  local simulation. Do not let it rot.

### 4.4 Rollout order

Do these in sequence — each one de-risks the next:

1. **Co-locate ETL with the DB** (§1.3) — biggest win, smallest change
2. **Per-chain matrix in GitHub Actions** (§2.2) — isolation + parallelism
3. **Onboard chains incrementally**, 5 at a time, with the DQ gates already on (§4.2)
4. **Entity resolution / canonical products** (§2.3) — the prerequisite for everything else
5. **Promotions**: schema → ETL → pure rule engine → UI breakdown (§3)
6. **History tier** in ClickHouse/Parquet, after measuring real daily churn (§1.2)
7. **Search index** for autocomplete, once product count clears ~150K

---

## Summary of recommendations

| Question | Answer |
|---|---|
| Storage needed? | **~3.2 GB live** (measured extrapolation); history **4–6 GB/yr** columnar, vs 632 GB/yr done naively |
| ETL RAM/CPU? | **2 GB / 2–4 vCPU** — streaming already solved OOM; **network distance is the real bottleneck** |
| API/DB sizing? | API 2 vCPU / 2–4 GB × ≥2 instances; DB 4–8 vCPU / **16 GB** so the working set stays in the buffer pool |
| NoSQL? | **No** — the data is relational. Split by workload (OLTP + OLAP), not by volume |
| Data warehouse? | **Yes, for history only** — ClickHouse or Parquet |
| Spark? | **No** — 18M rows is small data; the overhead exceeds the work |
| Airflow? | **Not yet** — restructure GitHub Actions to a per-chain matrix first; Prefect/Dagster when backfills and lineage are needed |
| Text search at scale? | **Move matching to ETL time** (canonical products + `product_map`); query time becomes an indexed join; dedicated search index for autocomplete only |
| Promotions model? | Rule-based `promotions` + `promotion_items`, time-bound, joined on canonical ids — model the rule, never parse the description at query time |
| Cart algorithm? | Base cost → active promos → evaluate → resolve conflicts (greedy, exact for small sets) → rank. Pure, DB-free, unit-testable |
