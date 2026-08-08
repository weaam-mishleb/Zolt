"""Record matrix ETL outcomes and reject successful-looking no-op runs.

Each matrix runner publishes one tiny JSON artifact. The report job downloads
all of them and runs ``check``; artifacts are necessary because GitHub does not
aggregate per-step outcomes from matrix children into ``needs.load``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def write_record(
    output: Path,
    *,
    chain: str,
    job_status: str,
    skipped: bool,
    price_loader: str,
    promotion_loader: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "chain": chain,
                "job_status": job_status,
                "skipped": skipped,
                "price_loader": price_loader,
                "promotion_loader": promotion_loader,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def evaluate(
    records: list[dict],
    *,
    planned: int,
    require_promotions: bool,
) -> tuple[dict[str, int], list[str]]:
    chains = {record.get("chain") for record in records if record.get("chain")}
    summary = {
        "planned": planned,
        "reported": len(chains),
        "skipped": sum(record.get("skipped") is True for record in records),
        "price_loaders": sum(record.get("price_loader") == "success" for record in records),
        "promotion_loaders": sum(
            record.get("promotion_loader") == "success" for record in records
        ),
    }
    errors: list[str] = []
    if summary["reported"] != planned:
        errors.append(
            f"received {summary['reported']} unique chain outcomes for {planned} planned chains"
        )
    if records and summary["skipped"] == len(records):
        errors.append("all chain jobs skipped; the ETL loaded nothing")
    elif summary["price_loaders"] == 0:
        errors.append("zero price loaders completed successfully")
    if require_promotions and summary["promotion_loaders"] == 0:
        errors.append("zero promotion loaders completed successfully")
    return summary, errors


def read_records(directory: Path) -> list[dict]:
    records = []
    for path in sorted(directory.rglob("*.json")):
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read outcome {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"outcome {path} is not a JSON object")
        records.append(value)
    return records


def _as_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return normalized == "true"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser("record")
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--chain", required=True)
    record.add_argument("--job-status", required=True)
    record.add_argument("--skipped", type=_as_bool, required=True)
    record.add_argument("--price-loader", required=True)
    record.add_argument("--promotion-loader", required=True)

    check = commands.add_parser("check")
    check.add_argument("--directory", type=Path, required=True)
    check.add_argument("--planned", type=int, required=True)
    check.add_argument("--require-promotions", type=_as_bool, required=True)

    args = parser.parse_args()
    if args.command == "record":
        write_record(
            args.output,
            chain=args.chain,
            job_status=args.job_status,
            skipped=args.skipped,
            price_loader=args.price_loader,
            promotion_loader=args.promotion_loader,
        )
        return

    try:
        records = read_records(args.directory)
    except ValueError as exc:
        print(f"::error::{exc}")
        sys.exit(1)
    summary, errors = evaluate(
        records,
        planned=args.planned,
        require_promotions=args.require_promotions,
    )
    print("ETL loader outcomes: " + " · ".join(f"{key}={value}" for key, value in summary.items()))
    if errors:
        for error in errors:
            print(f"::error::{error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
