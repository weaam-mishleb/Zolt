"""Load the price-transparency PROMOTION feed into promotions / promotion_items.

The promotion feed is far less uniform than the price feed: the 28 promo files
in the dataset fall into THREE incompatible families (ITEMS / GROUPS / FLAT).
Format handling therefore lives in `etl.promo_formats`; this module owns only
the streaming, resolution and upsert machinery, and is format-agnostic.

Prerequisite: `python -m etl.canonical` must have populated product_map —
promotion items are linked by canonical_id, which is what lets a promotion
published against one chain's barcode apply to the same product elsewhere.

Run:
    python -m etl.promotions --chains shufersal
    python -m etl.promotions --full            # promo_full_file_*.csv
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import bindparam, text

from .config import (
    CHAINS,
    CHUNK_SIZE,
    DEFAULT_DATA_DIR,
    PROMOTION_WRITE_BATCH_SIZE,
    promo_file,
)
from .loader import Loader
from .normalize import clean_str, norm_code, parse_dt, product_key
from .promo_formats import EXTRACTORS, classify_reward, detect_family

_PROMO_UPSERT = text(
    """
    INSERT INTO promotions
        (chain_id, store_id, promo_id_src, description, reward_kind,
         discount_rate, discount_amount, discounted_price, min_qty, max_qty,
         min_basket_amount, allow_stacking, club_id,
         reward_type_src, discount_type_src, starts_at, ends_at)
    VALUES
        (:chain_id, :store_id, :promo_id_src, :description, :reward_kind,
         :discount_rate, :discount_amount, :discounted_price, :min_qty, :max_qty,
         :min_basket_amount, :allow_stacking, :club_id,
         :reward_type_src, :discount_type_src, :starts_at, :ends_at)
    ON DUPLICATE KEY UPDATE
        description       = VALUES(description),
        reward_kind       = VALUES(reward_kind),
        discount_rate     = VALUES(discount_rate),
        discount_amount   = VALUES(discount_amount),
        discounted_price  = VALUES(discounted_price),
        min_qty           = VALUES(min_qty),
        max_qty           = VALUES(max_qty),
        min_basket_amount = VALUES(min_basket_amount),
        allow_stacking    = VALUES(allow_stacking),
        club_id           = VALUES(club_id),
        starts_at         = VALUES(starts_at),
        ends_at           = VALUES(ends_at)
    """
)

_ITEM_UPSERT = text(
    "INSERT IGNORE INTO promotion_items (promotion_id, canonical_id, is_gift) "
    "VALUES (:promotion_id, :canonical_id, :is_gift)"
)

_SELECT_PROMO_IDS = text(
    "SELECT id, promo_id_src FROM promotions "
    "WHERE chain_id = :chain AND store_id = :store AND promo_id_src IN :srcs"
).bindparams(bindparam("srcs", expanding=True))

_SELECT_CANONICAL = text(
    "SELECT p.barcode, m.canonical_id FROM products p "
    "JOIN product_map m ON m.product_id = p.id WHERE p.barcode IN :bcs"
).bindparams(bindparam("bcs", expanding=True))


def _clamp(value, limit: int):
    """Never let a long free-text field abort a whole load.

    The feed's text columns have no documented bound — `clubid` turned out to
    carry a 105-char membership expression, not an id. Truncating one oversized
    string is always better than losing the batch it happened to sit in.
    """
    s = clean_str(value)
    return s[:limit] if s and len(s) > limit else s


def _validity(header: dict) -> tuple:
    """Resolve start/end into real DATETIMEs across both column conventions.

    ITEMS/FLAT ship date + hour separately; GROUPS ships a combined datetime.
    """
    def combine(date_val, hour_val):
        d = clean_str(date_val)
        if not d:
            return None
        if " " in d or "T" in d:          # already a full datetime
            return parse_dt(d)
        h = clean_str(hour_val) or "00:00:00"
        return parse_dt(f"{d} {h}") or parse_dt(d)

    if "_start_datetime" in header:
        return (
            combine(header.get("_start_datetime"), header.get("_start_hour")),
            combine(header.get("_end_datetime"), header.get("_end_hour")),
        )
    return (
        combine(header.get("_start_date"), header.get("_start_hour")),
        combine(header.get("_end_date"), header.get("_end_hour")),
    )


class _CanonicalCache:
    """barcode → canonical_id, batched and memoized (misses cached too).

    Promotions reference the same barcodes repeatedly, and a promo pointing at a
    product no chain currently prices is common — re-querying either case per
    row would dominate runtime.
    """

    def __init__(self, engine, batch_size: int = 500):
        self.engine = engine
        self.batch_size = batch_size
        self._cache: dict[str, int | None] = {}

    def resolve(self, barcodes: set[str]) -> dict[str, int]:
        unknown = [b for b in barcodes if b not in self._cache]
        for i in range(0, len(unknown), self.batch_size):
            chunk = unknown[i : i + self.batch_size]
            with self.engine.connect() as conn:
                found = dict(conn.execute(_SELECT_CANONICAL, {"bcs": chunk}).all())
            for b in chunk:
                self._cache[b] = found.get(b)
        return {b: cid for b in barcodes if (cid := self._cache.get(b)) is not None}


def load_chain(
    engine,
    slug: str,
    data_dir: Path,
    *,
    full: bool,
    stats: Counter,
    dry_run: bool = False,
    write_batch_size: int = PROMOTION_WRITE_BATCH_SIZE,
) -> None:
    from .run import _read_csv_chunks           # shared streaming + forward-fill reader

    path = promo_file(data_dir, slug, full=full)
    if not path.exists():
        print(f"  ! missing promo file: {path.name} — skipping {slug}", file=sys.stderr)
        return

    with engine.connect() as conn:
        store_map = {
            (c, sc): sid
            for sid, c, sc in conn.execute(text("SELECT id, chain_id, store_code FROM stores"))
        }

    cache = _CanonicalCache(engine)
    # Same protections as the price loader: batches sorted into a shared lock
    # order, each its OWN transaction, READ COMMITTED, jittered deadlock retry.
    # Loader owns the bounded executemany loop: every 5,000 rows get their own
    # short READ COMMITTED transaction and retry boundary.  Do not turn the
    # whole chain into one transaction — a full feed can approach one million
    # links, which would retain locks/undo until the end and make one timeout
    # replay all of it.
    writer = Loader(engine, batch_size=write_batch_size)
    family, extract = None, None
    chain_promos = chain_links = 0

    for batch in _read_csv_chunks(path, CHUNK_SIZE):
        if extract is None:                     # detect once, from the real header
            family = detect_family(batch[0].keys())
            extract = EXTRACTORS.get(family)
            if extract is None:
                print(f"  ! {slug}: unrecognised promo format — skipping", file=sys.stderr)
                stats["unknown_format"] += 1
                return
            print(f"  · {slug:<12} format={family}", flush=True)

        promos: list[dict] = []
        for raw in batch:
            stats["rows_read"] += 1
            chain_id = clean_str(raw.get("chainid"))
            store_code = norm_code(raw.get("storeid"))
            if not chain_id or not store_code:
                stats["rows_bad"] += 1
                continue
            sid = store_map.get((chain_id, store_code))
            if sid is None:
                stats["no_store"] += 1
                continue

            for header, items in extract(raw):
                if not header.get("promo_id_src"):
                    stats["rows_bad"] += 1
                    continue
                starts_at, ends_at = _validity(header)
                promos.append({
                    "chain_id": chain_id,
                    "store_id": sid,
                    "promo_id_src": header["promo_id_src"],
                    "description": _clamp(header.get("description"), 255),
                    "reward_kind": classify_reward(header),
                    "discount_rate": header.get("discount_rate"),
                    "discount_amount": header.get("discount_amount"),
                    "discounted_price": header.get("discounted_price"),
                    "min_qty": header.get("min_qty") or 1,
                    "max_qty": (mq if (mq := header.get("max_qty")) and mq > 0 else None),
                    "min_basket_amount": header.get("min_basket_amount"),
                    # `allowmultiplediscounts` drives conflict resolution downstream
                    "allow_stacking": 1 if clean_str(raw.get("allowmultiplediscounts")) == "1" else 0,
                    "club_id": _clamp(raw.get("clubid"), 255),
                    "reward_type_src": header.get("reward_type_src"),
                    "discount_type_src": header.get("discount_type_src"),
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "_items": items,
                })

        if not promos:
            continue

        # 1. upsert headers (idempotent on (chain_id, store_id, promo_id_src))
        headers = [{k: v for k, v in p.items() if not k.startswith("_")} for p in promos]
        if not dry_run:
            writer.upsert_many(
                _PROMO_UPSERT, headers, ("chain_id", "store_id", "promo_id_src"),
                "promotions upsert",
            )

        # 2. read back surrogate ids
        #
        # A dry run has none to read back — nothing was written — so the natural
        # key stands in for the surrogate. It is only ever used as a dict key for
        # de-duplicating links, so the counts below come out identical either way.
        id_of: dict[tuple[str, int, str], object] = {}
        if dry_run:
            for p in promos:
                key = (p["chain_id"], p["store_id"], p["promo_id_src"])
                id_of[key] = key
        else:
            by_store: dict[tuple[str, int], list[str]] = {}
            for p in promos:
                by_store.setdefault((p["chain_id"], p["store_id"]), []).append(p["promo_id_src"])
            with engine.connect() as conn:
                for (chain, sid), srcs in by_store.items():
                    # Keep the readback bounded for the same reason as writes:
                    # a full catalogue can carry thousands of source ids for a
                    # branch, and one giant expanding IN list stresses both the
                    # client and MySQL's parser.
                    for i in range(0, len(srcs), write_batch_size):
                        for pid, src in conn.execute(
                            _SELECT_PROMO_IDS,
                            {
                                "chain": chain,
                                "store": sid,
                                "srcs": srcs[i : i + write_batch_size],
                            },
                        ).all():
                            id_of[(chain, sid, src)] = pid

        # 3. barcode → canonical_id, then link.
        #    The promo feed carries raw item codes, but products.barcode stores
        #    non-GTIN codes namespaced per chain, so the same transform has to be
        #    applied on the way in — otherwise every short-code promotion silently
        #    resolves to nothing and only the unresolved counter would show it.
        wanted = {
            key
            for p in promos
            for i in p["_items"]
            if (key := product_key(i.get("itemcode"), p["chain_id"]))
        }
        canon = cache.resolve(wanted)
        stats["items_unresolved"] += len(wanted) - len(canon)

        links, seen = [], set()
        for p in promos:
            pid = id_of.get((p["chain_id"], p["store_id"], p["promo_id_src"]))
            if pid is None:
                continue
            for it in p["_items"]:
                cid = canon.get(product_key(it.get("itemcode"), p["chain_id"]))
                if cid is None:
                    continue
                key = (pid, cid, it.get("is_gift", 0))
                if key in seen:            # the feed repeats items within a promo
                    continue
                seen.add(key)
                links.append({"promotion_id": pid, "canonical_id": cid,
                              "is_gift": it.get("is_gift", 0)})

        # promotion_items is the hottest write in the whole pipeline, for a
        # reason that is easy to miss: its FK points at canonical_products, and
        # canonical rows are SHARED between chains by design. So each INSERT
        # takes a shared lock on a parent row that a sibling runner's
        # etl.canonical may be holding exclusively — a cycle no amount of
        # INSERT IGNORE avoids, because IGNORE suppresses the duplicate-key
        # ERROR, never the lock taken to detect it.
        if not dry_run:
            writer.upsert_many(
                _ITEM_UPSERT, links, ("promotion_id", "canonical_id", "is_gift"),
                "promotion_items upsert",
            )

        stats["promotions"] += len(promos)
        stats["links"] += len(links)
        chain_promos += len(promos)
        chain_links += len(links)

    print(f"    {slug:<12} promotions={chain_promos:<7} item links={chain_links:,}")


def run(
    chains: list[str],
    data_dir: Path,
    *,
    full: bool,
    dry_run: bool = False,
    write_batch_size: int = PROMOTION_WRITE_BATCH_SIZE,
) -> Counter:
    from backend.app.db import engine

    t0 = time.time()
    stats: Counter = Counter()
    print(f"Zolt promotions — data_dir={data_dir}  chains={chains}  "
          f"{'full' if full else 'snapshot'}"
          f"{'  [DRY RUN — nothing is written]' if dry_run else ''}", flush=True)
    for slug in chains:
        load_chain(
            engine,
            slug,
            data_dir,
            full=full,
            stats=stats,
            dry_run=dry_run,
            write_batch_size=write_batch_size,
        )

    print("─" * 56)
    print(f"  rows read          : {stats['rows_read']:,}")
    print(f"  rows skipped       : {stats['rows_bad']:,} (unusable)")
    print(f"  promo w/o store    : {stats['no_store']:,}")
    print(f"  promotions upserted: {stats['promotions']:,}")
    print(f"  item links         : {stats['links']:,}")
    print(f"  items unresolved   : {stats['items_unresolved']:,} (barcode not in product_map)")
    print(f"  elapsed            : {time.time() - t0:.1f}s")
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description="Zolt promotions loader")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    p.add_argument("--chains", nargs="*", help="chain slugs (default: all configured)")
    p.add_argument("--full", action="store_true", help="use promo_full_file_*.csv")
    p.add_argument(
        "--write-batch-size",
        type=int,
        default=PROMOTION_WRITE_BATCH_SIZE,
        help="rows per promotion/header executemany transaction (default: 5000)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and resolve everything but write nothing — prints the same counters, "
             "so a shortfall can be located without touching the promotions tables",
    )
    args = p.parse_args()
    if args.write_batch_size < 1:
        p.error("--write-batch-size must be >= 1")
    run(
        args.chains or list(CHAINS),
        Path(args.data_dir),
        full=args.full,
        dry_run=args.dry_run,
        write_batch_size=args.write_batch_size,
    )


if __name__ == "__main__":
    main()
