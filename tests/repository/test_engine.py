from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from trader_agent.repository.engine import create_db_engine, init_schema


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "test.db")
    init_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"positions", "position_transactions", "closed_positions"}.issubset(tables)
