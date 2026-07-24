#!/usr/bin/env python3
"""
Uji proksi jejak akumulasi/distribusi — data SINTETIS, tanpa network.

Memvalidasi:
  - Akumulasi diam-diam (harga flat, A/D & OBV naik, volume ramai saat naik)
    -> verdict AKUMULASI + flag absorpsi.
  - Distribusi (harga naik tapi A/D & OBV turun) -> verdict DISTRIBUSI.
  - Data kurang -> None.
  - Score selalu -1..+1.

Jalankan: python -m tests.test_footprint
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant.analysis.footprint import compute_footprint
from quant.analysis.scoring import compute_features


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        raise AssertionError(msg)


def _base(n: int = 260, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = 1000 + np.cumsum(rng.normal(0, 3, n))
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.normal(1e6, 1e5, n).clip(min=1),
    }, index=dates)
    return df


def _set_bar(df: pd.DataFrame, pos: int, open_px: float, close_px: float,
             volume: float) -> None:
    """Bangun OHLC realistis: close dekat high saat naik, dekat low saat turun."""
    hi = max(open_px, close_px) * 1.004
    lo = min(open_px, close_px) * 0.996
    df.iloc[pos, df.columns.get_loc("open")] = open_px
    df.iloc[pos, df.columns.get_loc("close")] = close_px
    df.iloc[pos, df.columns.get_loc("high")] = hi
    df.iloc[pos, df.columns.get_loc("low")] = lo
    df.iloc[pos, df.columns.get_loc("volume")] = volume


def test_accumulation() -> None:
    print("\n== Akumulasi diam-diam (absorpsi) ==")
    df = _base()
    # 20 bar terakhir: harga DITEKAN flat/turun tipis, tapi hari-naik bervolume
    # jauh lebih besar (penyerapan) -> A/D & OBV naik.
    w = 20
    px = float(df["close"].iloc[-w - 1])
    for i in range(w):
        pos = -w + i
        up = i % 2 == 0
        close_px = px + (1.0 if up else -1.2)
        _set_bar(df, pos, px, close_px, 5e6 if up else 8e5)
        px = close_px
    feat = compute_features(df)
    fp = compute_footprint(feat, "TEST.JK", lookback=w)
    print(f"     verdict={fp.verdict} conf={fp.confidence} score={fp.score} "
          f"absorpsi={fp.absorption} volratio={fp.vol_updown_ratio}")
    check(fp is not None, "footprint tidak None")
    check(-1.0 <= fp.score <= 1.0, f"score dalam -1..1 ({fp.score})")
    check(fp.verdict == "AKUMULASI", f"verdict AKUMULASI (dapat {fp.verdict})")
    check(fp.vol_updown_ratio > 1.0, "volume naik > volume turun")
    check(fp.absorption, "flag absorpsi menyala (harga flat + akumulasi)")


def test_distribution() -> None:
    print("\n== Distribusi ke kekuatan ==")
    df = _base(seed=11)
    w = 20
    px = float(df["close"].iloc[-w - 1])
    for i in range(w):
        pos = -w + i
        up = i % 2 == 0
        # harga NAIK bertahap, tapi volume besar justru di hari TURUN (pelepasan)
        close_px = px + (1.5 if up else -0.5)
        _set_bar(df, pos, px, close_px, 8e5 if up else 5e6)
        px = close_px
    feat = compute_features(df)
    fp = compute_footprint(feat, "TEST.JK", lookback=w)
    print(f"     verdict={fp.verdict} conf={fp.confidence} score={fp.score} "
          f"volratio={fp.vol_updown_ratio}")
    check(fp.verdict == "DISTRIBUSI", f"verdict DISTRIBUSI (dapat {fp.verdict})")
    check(fp.vol_updown_ratio < 1.0, "volume turun > volume naik")
    check(not fp.absorption, "absorpsi TIDAK menyala saat distribusi")


def test_insufficient() -> None:
    print("\n== Data kurang ==")
    df = _base(n=10)
    feat = compute_features(df)
    fp = compute_footprint(feat, "TEST.JK", lookback=20)
    check(fp is None, "data < lookback -> None")


if __name__ == "__main__":
    print("Menjalankan uji jejak akumulasi/distribusi...")
    test_accumulation()
    test_distribution()
    test_insufficient()
    print("\nSemua uji LULUS. Proksi jejak berfungsi.\n")
