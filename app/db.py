from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .config import Settings


@contextmanager
def connect(settings: Settings) -> Iterator[Any]:
    if not settings.database_url:
        raise RuntimeError('DATABASE_URL is not configured')
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=False)
    try:
        yield conn
    finally:
        conn.close()


def fetch_one(settings: Settings, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def fetch_all(settings: Settings, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
    return [dict(row) for row in rows]


def execute(settings: Settings, sql: str, params: tuple[Any, ...] = ()) -> None:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
