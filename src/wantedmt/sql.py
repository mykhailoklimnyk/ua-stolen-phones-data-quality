"""One-row query helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import duckdb


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> Any:
    """The first column of the only row. No row means the query is wrong, not that the …"""
    row = con.execute(sql, list(params)).fetchone()
    assert row is not None, sql

    return row[0]


def count(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> int:
    value = scalar(con, sql, params)

    return int(value) if value is not None else 0
