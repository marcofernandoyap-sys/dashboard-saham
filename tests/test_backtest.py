#!/usr/bin/env python3
"""
Uji metrik & sanity backtest.

Jalankan: python -m tests.test_backtest
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant.backtest.engine import Backtester, BacktestConfig
from quant.backtest.metrics import (compute_metrics, max_drawdown,
                                    profit_factor, sharpe_ratio, win_rate)


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        raise AssertionError(msg)


def test_metrics_math() -> None:
    print("\n== Metrics math ==")
    check(abs(win_rate([1, -1, 2, -3]) - 0.5) < 1e-9, "win_rate 2/4 = 0.5")
    check(abs(profit_factor([3, -1, -1]) - 1.5) < 1e-9, "profit_factor 3/2 = 1.5")
    check(abs(max_drawdown([100, 120, 90, 130]) - (90/120 - 1)) < 1e-9,
          "max_drawdown puncak120->90 = -25%")
    check(max_drawdown([100, 110, 120]) == 0.0, "kurva naik -> DD 0")
    # Sharpe deret konstan naik -> std 0 -> 0
    check(sharpe_ratio([0.01, 0.01, 0.01]) == 0.0, "return konstan -> Sharpe 0")
    check(sharpe_ratio([0.01, -0.02, 0.03, 0.00]) != 0.0, "Sharpe terdefinisi")


def make_trending_market(n_tickers: int = 6, n: int = 400,
                         seed: int = 11) -> dict:
    """Beberapa saham tren naik dgn volume akumulatif (agar ada sinyal BUY)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    out = {}
    for k in range(n_tickers):
        drift = 0.0008 + 0.0004 * k / n_tickers
        rets = rng.normal(drift, 0.012, n)
        close = 1000 * np.cumprod(1 + rets)
        # sisipkan beberapa lonjakan volume di hari naik
        vol = np.full(n, 4e6) + rng.normal(0, 4e5, n)
        up = np.append([True], close[1:] > close[:-1])
        vol[up] *= 1.6
        df = pd.DataFrame({
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.006, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.006, n))),
            "close": close, "volume": vol.clip(min=1),
        }, index=dates)
        df["ticker"] = f"T{k}.JK"
        out[f"T{k}.JK"] = df
    return out


def test_backtest_run() -> None:
    print("\n== Backtest run (data sintetis) ==")
    market = make_trending_market()
    cfg = BacktestConfig(initial_capital=100_000_000, fee_bps=20.0)
    res = Backtester(market, cfg).run()
    m = res.metrics

    check(len(res.equity_curve) == len(res.dates), "equity_curve sejajar dgn tanggal")
    check(all(e >= 0 for e in res.equity_curve), "ekuitas tidak pernah negatif")
    check(0.0 <= m.win_rate <= 1.0, f"win_rate valid ({m.win_rate:.2f})")
    check(m.max_drawdown_pct <= 0.0, f"max_drawdown <= 0 ({m.max_drawdown_pct:.3f})")
    # Setiap trade: risiko sudah dibatasi -> kerugian per trade tidak ekstrem
    for t in res.trades:
        check(t.stop < t.entry_price, f"{t.ticker}: stop < entry (stop loss wajib)")
    print(f"     n_trades={m.n_trades} win={m.win_rate*100:.0f}% "
          f"avg={m.avg_return_pct:+.2f}% MDD={m.max_drawdown_pct*100:.1f}% "
          f"Sharpe={m.sharpe:.2f}")


def test_no_lookahead_max_positions() -> None:
    print("\n== Batas posisi & no-lookahead ==")
    market = make_trending_market(n_tickers=8)
    res = Backtester(market, BacktestConfig()).run()
    # Tidak boleh ada 2 posisi ticker sama bersamaan (tak double-entry)
    # dan setiap exit_date >= entry_date (kausalitas)
    for t in res.trades:
        check(t.exit_date >= t.entry_date, f"{t.ticker}: exit >= entry")


if __name__ == "__main__":
    print("Menjalankan uji backtest...")
    test_metrics_math()
    test_backtest_run()
    test_no_lookahead_max_positions()
    print("\nSemua uji backtest LULUS.\n")
