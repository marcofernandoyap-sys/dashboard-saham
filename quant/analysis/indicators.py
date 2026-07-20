"""
Indikator teknikal + money flow, implementasi pure pandas/numpy.

Kenapa tidak pakai library `ta`/`pandas_ta`?
  - Menghindari masalah instalasi/versi (proyek jalan di Python 3.9).
  - Rumus eksplisit lebih mudah di-audit — penting karena sinyal ini
    dipakai untuk keputusan finansial.

Konvensi input: DataFrame dengan kolom lower-case: open, high, low, close,
volume, ber-index tanggal terurut naik. Semua fungsi mengembalikan pd.Series
(atau DataFrame untuk indikator multi-garis) sejajar dengan index input.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Trend & momentum
# --------------------------------------------------------------------------


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI Wilder (smoothing EMA alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    # Ketika avg_loss == 0 -> RSI 100; ketika avg_gain == 0 -> RSI 0.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(avg_gain != 0, 0.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": hist}
    )


def bollinger_bands(close: pd.Series, period: int = 20,
                    n_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + n_std * std
    lower = mid - n_std * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    # %B: posisi harga relatif terhadap band (0 = lower, 1 = upper)
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
         "bb_width": width, "bb_pct_b": pct_b}
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low),
         (high - prev_close).abs(),
         (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# --------------------------------------------------------------------------
# Money flow / volume analysis
# --------------------------------------------------------------------------


def money_flow_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """MFI — 'RSI berbobot volume'. 0..100."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    raw_flow = tp * df["volume"]
    tp_diff = tp.diff()
    pos_flow = raw_flow.where(tp_diff > 0, 0.0)
    neg_flow = raw_flow.where(tp_diff < 0, 0.0)
    pos_sum = pos_flow.rolling(period).sum()
    neg_sum = neg_flow.rolling(period).sum()
    mfr = pos_sum / neg_sum.replace(0.0, np.nan)
    mfi = 100 - (100 / (1 + mfr))
    mfi = mfi.where(neg_sum != 0, 100.0)
    return mfi


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume — kumulatif volume searah arah harga."""
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).fillna(0.0).cumsum()


def chaikin_money_flow(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """CMF — rata-rata Money Flow Volume dibobot volume, -1..+1."""
    hl = (df["high"] - df["low"]).replace(0.0, np.nan)
    mf_mult = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl
    mf_vol = (mf_mult * df["volume"]).fillna(0.0)
    return mf_vol.rolling(period).sum() / df["volume"].rolling(period).sum()


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    VWAP kumulatif atas seluruh rentang data yang diberikan.
    Untuk intraday sejati, reset per hari — di sini dipakai untuk daily bars
    sebagai referensi rata-rata harga berbobot volume.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df["volume"].cumsum().replace(0.0, np.nan)
    return (tp * df["volume"]).cumsum() / cum_vol


def accumulation_distribution(df: pd.DataFrame) -> pd.Series:
    """A/D Line — deteksi akumulasi/distribusi tersembunyi."""
    hl = (df["high"] - df["low"]).replace(0.0, np.nan)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl
    ad = (clv.fillna(0.0) * df["volume"]).cumsum()
    return ad


def volume_spike(df: pd.DataFrame, window: int = 20,
                 mult: float = 2.0) -> pd.DataFrame:
    """
    Deteksi anomali volume (indikasi 'smart money' masuk/keluar).
    Return kolom: vol_avg, vol_ratio, is_spike (volume > mult * rata-rata).
    """
    avg = df["volume"].rolling(window).mean()
    ratio = df["volume"] / avg.replace(0.0, np.nan)
    return pd.DataFrame(
        {"vol_avg": avg, "vol_ratio": ratio, "is_spike": ratio > mult}
    )


def slope(series: pd.Series, window: int) -> pd.Series:
    """
    Kemiringan (per bar) via regresi linear pada `window` titik terakhir.
    Dipakai untuk mengukur arah tren OBV / A-D line secara robust.
    """
    idx = np.arange(window)
    denom = ((idx - idx.mean()) ** 2).sum()

    def _fit(vals: np.ndarray) -> float:
        if np.isnan(vals).any():
            return np.nan
        return float(((idx - idx.mean()) * (vals - vals.mean())).sum() / denom)

    return series.rolling(window).apply(_fit, raw=True)
