"""
Resample OHLCV harian -> mingguan (bar minggu berakhir Jumat).

Alasan ada di sini: `Storage.load_ohlcv` mengembalikan bar harian; kalau kita
mau menguji strategi pada timeframe berbeda tanpa ingest ulang, cukup resample
di layer aplikasi. Aturan aggregation OHLCV standar:
  open   = bar pertama minggu itu
  high   = max
  low    = min
  close  = bar terakhir
  volume = jumlah

Bar minggu ke-w berisi hanya data yang SUDAH terjadi di minggu itu -> tanpa
lookahead. Bar dgn NaN close (minggu tanpa perdagangan) di-drop.
"""
from __future__ import annotations

import pandas as pd


def to_weekly(df: pd.DataFrame, rule: str = "W-FRI") -> pd.DataFrame:
    """
    Resample DataFrame OHLCV harian -> mingguan.

    `df` diasumsikan ber-index DatetimeIndex (seperti hasil Storage.load_ohlcv).
    Kolom konstan (ticker, market) ikut dibawa apa adanya.
    """
    if df is None or df.empty:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("to_weekly: df.index harus DatetimeIndex")

    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    if "adj_close" in df.columns:
        agg["adj_close"] = "last"

    w = df.resample(rule).agg(agg).dropna(subset=["close"])

    # Bawa kolom konstan (nilai sama utk semua baris ticker yg sama)
    for col in ("ticker", "market"):
        if col in df.columns:
            w[col] = df[col].iloc[0]
    return w


def to_weekly_map(ohlcv_by_ticker: dict[str, pd.DataFrame],
                  rule: str = "W-FRI") -> dict[str, pd.DataFrame]:
    """Terapkan to_weekly ke seluruh map ticker->DataFrame."""
    return {t: to_weekly(df, rule) for t, df in ohlcv_by_ticker.items()}
