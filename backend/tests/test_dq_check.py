"""Tests for the data-quality gate.

This gate exists because of a real incident: a nationwide run reported 26 green
jobs while loading zero rows. The download had failed, the loader warned on
stderr and exited 0, and nothing noticed. These tests protect the thing that
would have caught it.
"""
from __future__ import annotations

import pytest

from scripts.dq_check import (
    MAX_BAD_PRICE_SHARE,
    MAX_BARCODE_NAME_SHARE,
    MIN_PRICES_PER_CHAIN,
    MIN_STORES_PER_CHAIN,
    chain_id_for,
    check_chain,
)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """Returns store count, price count, then barcode-named share — the order
    check_chain asks for them."""

    def __init__(self, stores, prices, barcode_named):
        self._answers = [stores, prices, barcode_named]

    def execute(self, *_a, **_kw):
        return _FakeResult(self._answers.pop(0))

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeEngine:
    """The gate's reads now go through Loader's retry policy, so the fake has to
    satisfy the same small surface a real Engine gives it."""

    def __init__(self, stores, prices, barcode_named=0.0):
        self.stores, self.prices = stores, prices
        self.barcode_named = barcode_named

    def connect(self):
        return _FakeConn(self.stores, self.prices, self.barcode_named)

    def execution_options(self, **_kw):
        return self

    def dispose(self):
        pass


@pytest.fixture
def store_csv(tmp_path):
    """A minimal store file carrying a chain id, like the real feed."""
    def _make(slug: str, chain_id: str = "7290999000001", blank_first: bool = False):
        p = tmp_path / f"store_file_{slug}.csv"
        rows = ["chainid,storeid,storename"]
        if blank_first:
            rows.append(",,")                      # grouped feed: identity omitted
        rows.append(f"{chain_id},1,סניף")
        p.write_text("\n".join(rows), encoding="utf-8")
        return tmp_path
    return _make


# ── chain_id resolution ─────────────────────────────────────────────────────
def test_chain_id_is_read_from_the_store_file(store_csv):
    d = store_csv("bareket", "7290875100001")
    assert chain_id_for("bareket", d) == "7290875100001"


def test_chain_id_skips_blank_rows(store_csv):
    """The feed's grouped layout leaves identity columns empty on some rows."""
    d = store_csv("bareket", "7290875100001", blank_first=True)
    assert chain_id_for("bareket", d) == "7290875100001"


def test_chain_id_is_none_when_the_store_file_is_absent(tmp_path):
    assert chain_id_for("nope", tmp_path) is None


def test_chain_id_is_none_when_the_file_has_no_usable_id(tmp_path):
    p = tmp_path / "store_file_x.csv"
    p.write_text("chainid,storeid\nNone,1\n,2\n", encoding="utf-8")
    assert chain_id_for("x", tmp_path) is None


# ── the gate itself ─────────────────────────────────────────────────────────
def test_missing_store_file_fails_with_a_download_hint(tmp_path):
    problems = check_chain(_FakeEngine(0, 0), "ghost", tmp_path)
    assert problems
    assert "download" in problems[0].lower()


def test_empty_load_fails_loudly(store_csv):
    """THE regression: this is what 26 'successful' jobs actually looked like."""
    d = store_csv("bareket")
    problems = check_chain(_FakeEngine(stores=0, prices=0), "bareket", d)
    assert len(problems) == 2
    assert any("no stores" in p for p in problems)
    assert any("no usable data" in p for p in problems)


def test_stores_without_prices_fails(store_csv):
    """Stores loaded but the price file was empty — still a failed load."""
    d = store_csv("bareket")
    problems = check_chain(_FakeEngine(stores=12, prices=0), "bareket", d)
    assert len(problems) == 1
    assert "prices" in problems[0]


def test_a_trickle_of_prices_still_fails(store_csv):
    d = store_csv("bareket")
    problems = check_chain(_FakeEngine(stores=3, prices=MIN_PRICES_PER_CHAIN - 1), "bareket", d)
    assert problems


def test_a_healthy_chain_passes(store_csv):
    d = store_csv("shufersal")
    assert check_chain(_FakeEngine(stores=421, prices=1_291_647), "shufersal", d) == []


def test_exactly_at_the_thresholds_passes(store_csv):
    d = store_csv("edge")
    assert check_chain(
        _FakeEngine(stores=MIN_STORES_PER_CHAIN, prices=MIN_PRICES_PER_CHAIN), "edge", d
    ) == []


# ── products named after their own item code ────────────────────────────────
def test_a_chain_loaded_with_barcode_names_fails(store_csv):
    """King Store et al. shipped `itemnm`, not `itemname`. The loader read only
    `itemname`, so every product loaded named after its item code — full row
    counts, green job, unusable data. This is the gate for that."""
    d = store_csv("king_store")
    problems = check_chain(_FakeEngine(stores=40, prices=300_000, barcode_named=1.0), "king_store", d)
    assert len(problems) == 1
    assert "item code" in problems[0]
    assert "normalize.py" in problems[0]


def test_a_normal_share_of_unnamed_products_passes(store_csv):
    """Real feeds always carry some nameless rows — 3% is the observed worst
    case for a chain read correctly, and must not fail the job."""
    d = store_csv("shufersal")
    assert check_chain(_FakeEngine(stores=421, prices=1_291_647, barcode_named=0.03),
                       "shufersal", d) == []


def test_barcode_name_share_is_not_checked_when_nothing_loaded(store_csv):
    """No products → SQL AVG returns NULL. The empty-load checks own that case;
    this one must not add a confusing second complaint."""
    d = store_csv("ghost")
    problems = check_chain(_FakeEngine(stores=0, prices=0, barcode_named=None), "ghost", d)
    assert not any("item code" in p for p in problems)


def test_barcode_name_threshold_clears_observed_noise():
    assert 0.03 < MAX_BARCODE_NAME_SHARE < 1.0


def test_bad_price_threshold_is_a_ratio_not_a_count():
    """48 junk rows in 2M is feed noise; a gate that fails on it gets ignored."""
    assert 0 < MAX_BAD_PRICE_SHARE < 0.01
    assert 48 / 1_982_479 < MAX_BAD_PRICE_SHARE
