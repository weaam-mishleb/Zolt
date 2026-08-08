"""Regression tests for the matrix ETL fake-green gate."""
from scripts.etl_outcome_gate import evaluate, read_records, write_record


def outcome(chain, *, skipped=False, price="success", promotions="success"):
    return {
        "chain": chain,
        "job_status": "success",
        "skipped": skipped,
        "price_loader": price,
        "promotion_loader": promotions,
    }


def test_thirty_skipped_chains_are_a_hard_failure():
    records = [outcome(str(i), skipped=True, price="skipped", promotions="skipped") for i in range(30)]
    summary, errors = evaluate(records, planned=30, require_promotions=True)

    assert summary["price_loaders"] == 0
    assert summary["promotion_loaders"] == 0
    assert "all chain jobs skipped; the ETL loaded nothing" in errors


def test_zero_promotion_loaders_fail_even_when_prices_loaded():
    records = [outcome("yellow", promotions="skipped"), outcome("dor", promotions="skipped")]
    summary, errors = evaluate(records, planned=2, require_promotions=True)

    assert summary["price_loaders"] == 2
    assert "zero promotion loaders completed successfully" in errors


def test_missing_matrix_outcome_fails_instead_of_looking_green():
    _summary, errors = evaluate([outcome("yellow")], planned=2, require_promotions=True)
    assert "received 1 unique chain outcomes for 2 planned chains" in errors


def test_one_real_loader_is_enough_when_every_chain_reported():
    records = [
        outcome("yellow"),
        outcome("dor", skipped=True, price="skipped", promotions="skipped"),
    ]
    summary, errors = evaluate(records, planned=2, require_promotions=True)

    assert summary["price_loaders"] == 1
    assert summary["promotion_loaders"] == 1
    assert errors == []


def test_promotion_gate_is_disabled_only_when_requested():
    records = [outcome("yellow", promotions="skipped")]
    _summary, errors = evaluate(records, planned=1, require_promotions=False)
    assert errors == []


def test_matrix_record_round_trips_through_the_artifact_file(tmp_path):
    path = tmp_path / "yellow.json"
    write_record(
        path,
        chain="yellow",
        job_status="success",
        skipped=False,
        price_loader="success",
        promotion_loader="success",
    )

    records = read_records(tmp_path)
    summary, errors = evaluate(records, planned=1, require_promotions=True)
    assert summary["reported"] == 1
    assert errors == []
