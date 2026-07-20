"""API-level validation tests for the basket endpoints (no DB required).

All requests here are rejected (or answered) before any SQL runs, so these
run DB-free like the rest of the unit suite.
"""
from __future__ import annotations

import os

os.environ.setdefault("SCHEDULER_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

client = TestClient(app)


def _items(n: int, quantity: float = 1):
    return [{"product_id": i + 1, "quantity": quantity} for i in range(n)]


def test_compare_rejects_more_than_50_items():
    r = client.post("/basket/compare", json={"city": "תל אביב", "items": _items(51)})
    assert r.status_code == 400
    assert "50" in r.json()["detail"]


def test_summary_rejects_more_than_50_items():
    r = client.post("/basket/summary", json={"items": _items(51)})
    assert r.status_code == 400
    assert "50" in r.json()["detail"]


def test_summary_rejects_negative_quantity():
    r = client.post("/basket/summary", json={"items": _items(2, quantity=-1)})
    assert r.status_code == 400


def test_summary_empty_basket_is_empty_not_error():
    r = client.post("/basket/summary", json={"items": []})
    assert r.status_code == 200
    assert r.json() == {"item_count": 0, "estimated_total": None, "chains": []}


def test_compare_still_rejects_empty_basket():
    r = client.post("/basket/compare", json={"city": "תל אביב", "items": []})
    assert r.status_code == 400
