"""Unit tests for the schema-file statement splitter (scripts/init_db).

Both cases below are real bugs this splitter hit while adding the promotions
schema: a semicolon inside a `--` comment, and a semicolon inside a
`COMMENT '...'` string literal. Each one silently cut a CREATE TABLE in half.
"""
from __future__ import annotations

from scripts.init_db import _split_on_semicolons, _statements


def test_splits_plain_statements():
    assert _statements("CREATE TABLE a (id INT); CREATE TABLE b (id INT);") == [
        "CREATE TABLE a (id INT)",
        "CREATE TABLE b (id INT)",
    ]


def test_semicolon_inside_line_comment_does_not_split():
    sql = """
    -- once mapped; the API only joins
    CREATE TABLE a (id INT);
    """
    assert _statements(sql) == ["CREATE TABLE a (id INT)"]


def test_semicolon_inside_string_literal_does_not_split():
    sql = "CREATE TABLE a (c INT COMMENT '1.000 = certain; lower goes to review');"
    out = _statements(sql)
    assert len(out) == 1
    assert "lower goes to review" in out[0]


def test_escaped_and_doubled_quotes_are_handled():
    assert len(_split_on_semicolons("SELECT 'it''s; fine'")) == 1
    assert len(_split_on_semicolons(r"SELECT 'a\'; b'")) == 1


def test_skips_use_and_create_database():
    sql = "USE zolt; CREATE DATABASE x; CREATE TABLE a (id INT);"
    assert _statements(sql) == ["CREATE TABLE a (id INT)"]


def test_blank_and_trailing_separators_ignored():
    assert _statements("CREATE TABLE a (id INT);;\n\n  ;") == ["CREATE TABLE a (id INT)"]


def test_real_schema_files_parse_into_executable_statements():
    """Guard the actual shipped schema — every file must yield ≥1 statement and
    no fragment may start mid-definition."""
    from backend.app.config import PROJECT_ROOT

    for path in sorted((PROJECT_ROOT / "db" / "init").glob("*.sql")):
        stmts = _statements(path.read_text(encoding="utf-8"))
        assert stmts, f"{path.name} produced no statements"
        for s in stmts:
            head = s.lstrip().lower()
            assert head.startswith(# prepare/execute/deallocate: MySQL 8 has no ADD COLUMN IF NOT EXISTS,
                # so 05_product_availability.sql builds the ALTER conditionally
                # and runs it as a prepared statement.
                ("create", "alter", "insert", "set", "drop",
                 "prepare", "execute", "deallocate")), (
                f"{path.name}: fragment does not start a statement → {s[:80]!r}"
            )
