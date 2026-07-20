"""
Penyimpanan data historis berbasis SQLite (stdlib only, tanpa dependency berat).

Skema:
  ohlcv(ticker, market, date, open, high, low, close, adj_close, volume)
    PRIMARY KEY (ticker, date) -> idempotent upsert, aman dijalankan berulang.

Alasan SQLite: portable, satu file, cukup untuk skala watchlist puluhan-ratusan
ticker dengan histori beberapa tahun. Bisa diganti ke Postgres/TimescaleDB nanti
lewat interface yang sama tanpa mengubah pemanggil.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from quant.config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv (
    ticker     TEXT    NOT NULL,
    market     TEXT    NOT NULL,
    date       TEXT    NOT NULL,          -- ISO YYYY-MM-DD
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    adj_close  REAL,
    volume     REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker ON ohlcv(ticker);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date   ON ohlcv(date);

CREATE TABLE IF NOT EXISTS ingestion_log (
    ticker      TEXT NOT NULL,
    market      TEXT NOT NULL,
    rows        INTEGER,
    start_date  TEXT,
    end_date    TEXT,
    fetched_at  TEXT NOT NULL
);
"""

_COLUMNS = ["ticker", "market", "date", "open", "high", "low",
            "close", "adj_close", "volume"]


class Storage:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ---------------------------------------------------------------- write
    def upsert_ohlcv(self, df: pd.DataFrame) -> int:
        """
        Upsert OHLCV. `df` wajib punya kolom _COLUMNS. Baris dengan
        (ticker, date) yang sama akan di-replace (idempotent).
        Return jumlah baris yang di-tulis.
        """
        if df.empty:
            return 0
        missing = set(_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"Kolom hilang untuk upsert_ohlcv: {sorted(missing)}")

        rows = df[_COLUMNS].itertuples(index=False, name=None)
        sql = (
            "INSERT INTO ohlcv (ticker, market, date, open, high, low, "
            "close, adj_close, volume) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET "
            "market=excluded.market, open=excluded.open, high=excluded.high, "
            "low=excluded.low, close=excluded.close, "
            "adj_close=excluded.adj_close, volume=excluded.volume"
        )
        with self._conn() as conn:
            conn.executemany(sql, rows)
        return len(df)

    def log_ingestion(self, ticker: str, market: str, rows: int,
                      start: str | None, end: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ingestion_log "
                "(ticker, market, rows, start_date, end_date, fetched_at) "
                "VALUES (?,?,?,?,?, datetime('now'))",
                (ticker, market, rows, start, end),
            )

    # ----------------------------------------------------------------- read
    def load_ohlcv(self, ticker: str,
                   start: str | None = None,
                   end: str | None = None) -> pd.DataFrame:
        """Load OHLCV satu ticker sebagai DataFrame ber-index tanggal (DatetimeIndex)."""
        query = "SELECT * FROM ohlcv WHERE ticker = ?"
        params: list[object] = [ticker]
        if start:
            query += " AND date >= ?"
            params.append(start)
        if end:
            query += " AND date <= ?"
            params.append(end)
        query += " ORDER BY date ASC"

        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    def tickers(self, market: str | None = None) -> list[str]:
        query = "SELECT DISTINCT ticker FROM ohlcv"
        params: list[object] = []
        if market:
            query += " WHERE market = ?"
            params.append(market)
        query += " ORDER BY ticker"
        with self._conn() as conn:
            return [r[0] for r in conn.execute(query, params).fetchall()]

    def last_date(self, ticker: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM ohlcv WHERE ticker = ?", (ticker,)
            ).fetchone()
        return row[0] if row and row[0] else None
