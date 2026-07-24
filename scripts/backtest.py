#!/usr/bin/env python3
"""
CLI: jalankan backtest portfolio atas data tersimpan dan cetak metrik.

Contoh:
    python -m scripts.backtest --capital 100000000
    python -m scripts.backtest --capital 100000000 --start 2024-01-01 --fee-bps 25

Menyimpan hasil ke data/backtests/ (dipakai gerbang 'no live tanpa backtest').
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.backtest.engine import (Backtester, BacktestConfig, build_regime,
                                    build_rs)
from quant.backtest.registry import live_readiness, save_result
from quant.config import SETTINGS
from quant.data.storage import Storage


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest portfolio watchlist IDX")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--fee-bps", type=float, default=20.0)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    storage = Storage()
    tickers = storage.tickers(market="IDX")   # indeks (^JKSE) BUKAN aset tradable
    if not tickers:
        print("Belum ada data. Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1

    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None
    regime_ok = build_regime(index_df, SETTINGS)
    rs_ok = build_rs(ohlcv, index_df, SETTINGS)
    if SETTINGS.regime.enabled and regime_ok is None:
        print(f"[peringatan] data indeks {SETTINGS.regime.index_ticker} tak ada -> "
              "filter regime NONAKTIF. Ingest: "
              f"python -m scripts.ingest --tickers {SETTINGS.regime.index_ticker} --market INDEX")
    if SETTINGS.rs.enabled and rs_ok is None:
        print(f"[peringatan] data indeks tak ada -> filter kekuatan relatif NONAKTIF.")

    cfg = BacktestConfig(initial_capital=args.capital, fee_bps=args.fee_bps,
                         start=args.start, end=args.end)
    result = Backtester(ohlcv, cfg, SETTINGS, regime_ok=regime_ok,
                        rs_ok=rs_ok).run()
    m = result.metrics

    print("\n" + "=" * 64)
    print("HASIL BACKTEST — " + SETTINGS.disclaimer)
    print("=" * 64)
    period = f"{result.dates[0]} s/d {result.dates[-1]}" if result.dates else "-"
    print(f"Periode           : {period} ({len(result.dates)} hari bursa)")
    print(f"Modal awal        : Rp{args.capital:,.0f}   fee {args.fee_bps:.0f} bps/sisi")
    print("-" * 64)
    print(f"Jumlah trade      : {m.n_trades}")
    print(f"Win rate          : {m.win_rate*100:.1f}%")
    print(f"Avg return/trade  : {m.avg_return_pct:+.2f}%  (median {m.median_return_pct:+.2f}%)")
    print(f"Profit factor     : {m.profit_factor:.2f}")
    print(f"Expectancy        : {m.expectancy_r:+.2f} R")
    print(f"Max drawdown      : {m.max_drawdown_pct*100:.1f}%")
    print(f"Sharpe (annual)   : {m.sharpe:.2f}")
    print(f"Total return      : {m.total_return_pct*100:+.1f}%   CAGR {m.cagr_pct*100:+.1f}%")
    if result.circuit_breaker_events:
        print("-" * 64)
        print(f"Circuit breaker   : {len(result.circuit_breaker_events)} event")
        for ev in result.circuit_breaker_events[:5]:
            print(f"   - {ev}")

    path = save_result(result, args.label)
    print("-" * 64)
    print(f"Hasil disimpan    : {path}")

    ready = live_readiness(SETTINGS)
    print("\nGerbang kesiapan LIVE:")
    if ready["allowed"]:
        print("  Lolos gerbang backtest. (Syarat paper-trading tetap dicek di fase eksekusi.)")
    else:
        for b in ready["blockers"]:
            print(f"  BLOKIR: {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
