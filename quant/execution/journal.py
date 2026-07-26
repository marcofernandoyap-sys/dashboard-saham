"""
Jurnal eksekusi: catat setiap fill ke SQLite & hitung rekam jejak paper-trading.

Rekam jejak inilah yang MEMBERI MAKAN gerbang kesiapan live (config.risk):
  - min_paper_trading_days  (default 60)  <- hari OBSERVASI, bukan hari ada fill
  - min_recorded_trades     (default 30)  <- jumlah fill paper

PENTING soal `n_days`: gerbang waktu ini mengukur berapa lama strategi diamati
di pasar NYATA lintas kondisi — TERMASUK hari saat strategi benar "diam" (mis.
regime off). Karena itu tiap hari `paper_daily` berjalan mencatat satu baris
"sesi observasi" di `exec_sessions`, dan `n_days` dihitung dari gabungan hari
sesi + hari fill. Kalau hanya dihitung dari hari-ada-fill, jam berhenti selama
downtrend — padahal "diam saat downtrend" justru perilaku yang ingin divalidasi.

Tabel `exec_fills` & `exec_sessions` terpisah dari data OHLCV; aman diinspeksi/dihapus.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from quant.config import DB_PATH, SETTINGS
from quant.execution.broker import Fill

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exec_fills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,          -- ISO UTC
    broker     TEXT NOT NULL,
    mode       TEXT NOT NULL,          -- paper | live
    ticker     TEXT NOT NULL,
    side       TEXT NOT NULL,          -- buy | sell
    qty        INTEGER NOT NULL,
    price      REAL NOT NULL,
    fee        REAL NOT NULL,
    status     TEXT NOT NULL,          -- filled | rejected
    order_id   TEXT,
    reason     TEXT
);
CREATE INDEX IF NOT EXISTS idx_fills_ts     ON exec_fills(ts);
CREATE INDEX IF NOT EXISTS idx_fills_mode   ON exec_fills(mode);

CREATE TABLE IF NOT EXISTS exec_sessions (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,             -- ISO UTC waktu sesi berjalan
    mode   TEXT NOT NULL,             -- paper | live
    note   TEXT                       -- mis. jumlah sinyal / "stand aside"
);
CREATE INDEX IF NOT EXISTS idx_sessions_mode ON exec_sessions(mode);
"""


class Journal:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record(self, fill: Fill, mode: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO exec_fills (ts, broker, mode, ticker, side, qty, "
                "price, fee, status, order_id, reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (fill.ts, fill.broker, mode, fill.ticker, fill.side, fill.qty,
                 fill.price, fill.fee, fill.status, fill.order_id, fill.reason),
            )

    def record_session(self, mode: str = "paper", note: str = "",
                       ts: str | None = None) -> None:
        """Catat satu HARI OBSERVASI (paper_daily berjalan), meski nol fill."""
        ts = ts or datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO exec_sessions (ts, mode, note) VALUES (?,?,?)",
                (ts, mode, note),
            )

    def paper_stats(self) -> dict:
        """
        Statistik rekam jejak PAPER (mode 'paper'):
          n_trades : jumlah fill terisi ('filled')
          n_days   : jumlah hari kalender unik DIAMATI — gabungan hari sesi
                     observasi + hari ada fill. Hari "diam" (nol fill) tetap
                     dihitung; jam waktu tidak berhenti saat regime off.
        """
        with self._conn() as c:
            n_trades = c.execute(
                "SELECT COUNT(*) FROM exec_fills "
                "WHERE mode='paper' AND status='filled'"
            ).fetchone()[0] or 0
            n_days = c.execute(
                "SELECT COUNT(DISTINCT day) FROM ("
                "  SELECT substr(ts,1,10) AS day FROM exec_sessions "
                "    WHERE mode='paper' "
                "  UNION "
                "  SELECT substr(ts,1,10) AS day FROM exec_fills "
                "    WHERE mode='paper' AND status='filled'"
                ")"
            ).fetchone()[0] or 0
        return {"n_trades": n_trades, "n_days": n_days}


def paper_readiness(journal: Journal, settings=SETTINGS) -> dict:
    """
    Apakah rekam jejak PAPER cukup untuk mengizinkan live? (gerbang kedua,
    melengkapi gerbang backtest di registry.live_readiness).
    """
    r = settings.risk
    stats = journal.paper_stats()
    blockers: list[str] = []
    if stats["n_days"] < r.min_paper_trading_days:
        blockers.append(
            f"paper-trading baru {stats['n_days']} hari "
            f"(<{r.min_paper_trading_days} minimum)")
    if stats["n_trades"] < r.min_recorded_trades:
        blockers.append(
            f"paper-trading baru {stats['n_trades']} trade "
            f"(<{r.min_recorded_trades} minimum)")
    return {"allowed": not blockers, "blockers": blockers, "stats": stats}
