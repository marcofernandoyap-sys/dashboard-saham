#!/usr/bin/env python3
"""
CLI: ambil data historis IDX (atau US) via yfinance dan simpan ke SQLite.

Contoh:
    python -m scripts.ingest --index LQ45 --period 3y
    python -m scripts.ingest --tickers BBCA.JK TLKM.JK --period 2y
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.data.ingestion import YFinanceProvider, ingest
from quant.data.storage import Storage
from quant.universe import idx_tickers


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest data harga ke SQLite")
    ap.add_argument("--index", default="LQ45", help="LQ45 atau IDX30")
    ap.add_argument("--tickers", nargs="*", help="override daftar ticker (mis. BBCA.JK)")
    ap.add_argument("--market", default="IDX")
    ap.add_argument("--period", default="3y", help="periode yfinance (mis. 3y, 5y)")
    ap.add_argument("--interval", default="1d")
    args = ap.parse_args()

    tickers = args.tickers or idx_tickers(args.index)
    print(f"Ingesting {len(tickers)} ticker (market={args.market}, "
          f"period={args.period})...")

    storage = Storage()
    provider = YFinanceProvider(market=args.market)
    results = ingest(tickers, provider, storage,
                     period=args.period, interval=args.interval)

    ok = sum(1 for v in results.values() if v > 0)
    fail = sum(1 for v in results.values() if v < 0)
    total_rows = sum(v for v in results.values() if v > 0)
    print(f"Selesai: {ok} sukses, {fail} gagal, total {total_rows:,} baris.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
