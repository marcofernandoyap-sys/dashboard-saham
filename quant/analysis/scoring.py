"""
Scoring engine.

Menggabungkan indikator jadi:
  - skor komposit -100..+100
  - klasifikasi Strong Buy / Buy / Hold / Sell / Strong Sell
  - alasan tekstual
  - metadata untuk signal logic (kategori indikator mana yang konfirmasi,
    konfirmasi volume, harga vs EMA50, saran stop loss berbasis ATR)

Dua KATEGORI indikator dipisah tegas supaya signal logic bisa mensyaratkan
konfirmasi dari >=2 kategori berbeda (trend + money flow), bukan 1 indikator saja:
  - TREND   : EMA alignment, RSI, MACD, Bollinger
  - MONEYFLOW: MFI, OBV, CMF, A/D line, volume spike

Setiap komponen menghasilkan nilai ternormalisasi di [-1, +1]. Skor kategori =
rata-rata komponennya. Skor komposit = rata-rata berbobot kategori * 100.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.analysis import indicators as ind
from quant.config import SETTINGS, IndicatorParams

# Bobot antar kategori (jumlah = 1.0). Money flow diberi bobot setara trend
# karena jadi fokus utama sistem.
CATEGORY_WEIGHTS = {"trend": 0.5, "moneyflow": 0.5}
# Ambang sebuah kategori dianggap "konfirmasi" arah (bullish/bearish).
CATEGORY_CONFIRM_THRESHOLD = 0.20


@dataclass
class Score:
    ticker: str
    date: str
    composite: float                         # -100..+100
    classification: str
    trend_score: float                       # -100..+100
    moneyflow_score: float                   # -100..+100
    confirming_categories: int               # jumlah kategori yg konfirmasi arah komposit
    volume_confirmed: bool                   # volume hari itu > mult * rata-rata
    above_ema_trend: bool                    # harga > EMA (require_above_ema)
    reasons: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["reasons"] = list(self.reasons)
        return d


def compute_features(df: pd.DataFrame,
                     p: IndicatorParams | None = None) -> pd.DataFrame:
    """Hitung semua indikator dan tempelkan sebagai kolom baru."""
    p = p or SETTINGS.indicators
    out = df.copy()
    close = out["close"]

    for period in p.ema_periods:
        out[f"ema_{period}"] = ind.ema(close, period)
    out["rsi"] = ind.rsi(close, p.rsi_period)
    macd = ind.macd(close, p.macd_fast, p.macd_slow, p.macd_signal)
    out = out.join(macd)
    bb = ind.bollinger_bands(close, p.bb_period, p.bb_std)
    out = out.join(bb)
    out["atr"] = ind.atr(out, p.atr_period)

    out["mfi"] = ind.money_flow_index(out, p.mfi_period)
    out["obv"] = ind.obv(out)
    out["obv_slope"] = ind.slope(out["obv"], p.obv_slope_window)
    out["cmf"] = ind.chaikin_money_flow(out, p.cmf_period)
    out["vwap"] = ind.vwap(out)
    out["ad"] = ind.accumulation_distribution(out)
    out["ad_slope"] = ind.slope(out["ad"], p.obv_slope_window)
    vs = ind.volume_spike(out, p.volume_avg_window, p.volume_spike_mult)
    out = out.join(vs)
    return out


def _clip(x: float) -> float:
    return float(np.clip(x, -1.0, 1.0))


def _trend_components(row: pd.Series, prev: pd.Series,
                      p: IndicatorParams) -> tuple[float, list[str]]:
    comps: list[float] = []
    reasons: list[str] = []
    close = row["close"]

    # 1) EMA alignment (tren menengah/panjang)
    ema_fast, ema_mid = row.get("ema_21"), row.get("ema_50")
    ema_slow = row.get("ema_200")
    align = 0.0
    if pd.notna(ema_mid):
        align += 0.5 if close > ema_mid else -0.5
    if pd.notna(ema_fast) and pd.notna(ema_mid):
        align += 0.25 if ema_fast > ema_mid else -0.25
    if pd.notna(ema_slow) and pd.notna(ema_mid):
        align += 0.25 if ema_mid > ema_slow else -0.25
    comps.append(_clip(align))
    if align > 0.5:
        reasons.append("harga di atas EMA50 & struktur EMA bullish (tren naik)")
    elif align < -0.5:
        reasons.append("harga di bawah EMA50 & struktur EMA bearish (tren turun)")

    # 2) RSI: bullish 50-70; overbought >70 dikurangi; oversold <30 = potensi rebound
    rsi = row.get("rsi")
    if pd.notna(rsi):
        if rsi >= 70:
            comps.append(-0.3)
            reasons.append(f"RSI {rsi:.0f} overbought (risiko koreksi)")
        elif rsi <= 30:
            comps.append(0.3)
            reasons.append(f"RSI {rsi:.0f} oversold (potensi rebound)")
        else:
            comps.append(_clip((rsi - 50) / 20.0))
            prev_rsi = prev.get("rsi")
            if pd.notna(prev_rsi) and rsi - prev_rsi >= 5:
                reasons.append(f"RSI menguat {prev_rsi:.0f}->{rsi:.0f}")

    # 3) MACD histogram + posisi terhadap signal
    macd_line, sig, hist = row.get("macd"), row.get("signal"), row.get("hist")
    if pd.notna(hist):
        m = 0.5 if hist > 0 else -0.5
        if pd.notna(macd_line) and pd.notna(sig):
            m += 0.5 if macd_line > sig else -0.5
        comps.append(_clip(m))
        prev_hist = prev.get("hist")
        if pd.notna(prev_hist) and prev_hist <= 0 < hist:
            reasons.append("MACD baru saja golden cross (momentum berbalik naik)")
        elif pd.notna(prev_hist) and prev_hist >= 0 > hist:
            reasons.append("MACD death cross (momentum berbalik turun)")

    # 4) Bollinger %B
    pct_b = row.get("bb_pct_b")
    if pd.notna(pct_b):
        if pct_b > 1.0:
            comps.append(-0.2)
            reasons.append("harga tembus atas Bollinger (overextended)")
        elif pct_b < 0.0:
            comps.append(0.2)
            reasons.append("harga di bawah Bollinger bawah (oversold)")
        else:
            comps.append(_clip((pct_b - 0.5) * 1.2))

    score = float(np.mean(comps)) if comps else 0.0
    return score, reasons


def _moneyflow_components(row: pd.Series, prev: pd.Series,
                          window_ago: pd.Series | None,
                          p: IndicatorParams) -> tuple[float, list[str]]:
    comps: list[float] = []
    reasons: list[str] = []

    # 1) MFI (RSI berbobot volume)
    mfi = row.get("mfi")
    if pd.notna(mfi):
        comps.append(_clip((mfi - 50) / 30.0))
        if window_ago is not None:
            mfi_prev = window_ago.get("mfi")
            if pd.notna(mfi_prev) and mfi - mfi_prev >= 15:
                reasons.append(
                    f"MFI naik {mfi_prev:.0f}->{mfi:.0f} (indikasi akumulasi)"
                )
        if mfi >= 80:
            reasons.append(f"MFI {mfi:.0f} overbought secara volume")

    # 2) OBV slope (akumulasi tersembunyi)
    obv_slope = row.get("obv_slope")
    if pd.notna(obv_slope):
        comps.append(0.6 if obv_slope > 0 else -0.6)
        if obv_slope > 0:
            reasons.append("OBV menaik (volume mendukung kenaikan = akumulasi)")
        else:
            reasons.append("OBV menurun (distribusi)")

    # 3) CMF
    cmf = row.get("cmf")
    if pd.notna(cmf):
        comps.append(_clip(cmf * 5.0))
        if cmf > 0.1:
            reasons.append(f"CMF +{cmf:.2f} (tekanan beli dominan)")
        elif cmf < -0.1:
            reasons.append(f"CMF {cmf:.2f} (tekanan jual dominan)")

    # 4) A/D line slope
    ad_slope = row.get("ad_slope")
    if pd.notna(ad_slope):
        comps.append(0.5 if ad_slope > 0 else -0.5)

    # 5) Volume spike diarahkan oleh arah harga hari itu
    if bool(row.get("is_spike", False)):
        up_day = row["close"] >= prev.get("close", row["close"])
        ratio = row.get("vol_ratio", float("nan"))
        if up_day:
            comps.append(0.7)
            reasons.append(
                f"volume spike {ratio:.1f}x pada hari naik (smart money masuk?)"
            )
        else:
            comps.append(-0.7)
            reasons.append(
                f"volume spike {ratio:.1f}x pada hari turun (distribusi?)"
            )

    score = float(np.mean(comps)) if comps else 0.0
    return score, reasons


def classify(composite: float, s=SETTINGS.signal) -> str:
    if composite >= s.strong_buy_score_threshold:
        return "Strong Buy"
    if composite >= s.buy_score_threshold:
        return "Buy"
    if composite <= s.strong_sell_score_threshold:
        return "Strong Sell"
    if composite <= s.sell_score_threshold:
        return "Sell"
    return "Hold"


def score_ticker(ticker: str, features: pd.DataFrame,
                 settings=SETTINGS) -> Score | None:
    """Skor pada bar terakhir dari DataFrame fitur (hasil compute_features)."""
    if features is None or len(features) < 2:
        return None

    p = settings.indicators
    row = features.iloc[-1]
    prev = features.iloc[-2]
    win = p.obv_slope_window
    window_ago = features.iloc[-win] if len(features) > win else None

    trend, trend_reasons = _trend_components(row, prev, p)
    mflow, mflow_reasons = _moneyflow_components(row, prev, window_ago, p)

    composite = (
        CATEGORY_WEIGHTS["trend"] * trend
        + CATEGORY_WEIGHTS["moneyflow"] * mflow
    ) * 100.0
    composite = float(np.clip(composite, -100.0, 100.0))

    # Berapa kategori yang konfirmasi arah komposit?
    direction = np.sign(composite)
    confirming = 0
    if direction != 0:
        if np.sign(trend) == direction and abs(trend) >= CATEGORY_CONFIRM_THRESHOLD:
            confirming += 1
        if np.sign(mflow) == direction and abs(mflow) >= CATEGORY_CONFIRM_THRESHOLD:
            confirming += 1

    # Konfirmasi volume & posisi terhadap EMA tren
    vol_ratio = row.get("vol_ratio", float("nan"))
    volume_confirmed = bool(
        pd.notna(vol_ratio) and vol_ratio >= settings.signal.volume_confirm_mult
    )
    ema_col = f"ema_{settings.signal.require_above_ema}"
    ema_val = row.get(ema_col)
    above_ema = bool(pd.notna(ema_val) and row["close"] > ema_val)

    reasons = trend_reasons + mflow_reasons
    if not reasons:
        reasons.append("tidak ada sinyal indikator yang menonjol (netral)")

    keep = ["close", "rsi", "macd", "signal", "hist", "mfi", "cmf",
            "obv_slope", "ad_slope", "vol_ratio", "atr", "bb_pct_b",
            "ema_50", "ema_200", "vwap"]
    feats = {k: (float(row[k]) if pd.notna(row.get(k)) else None)
             for k in keep if k in row.index}

    return Score(
        ticker=ticker,
        date=str(features.index[-1].date())
        if hasattr(features.index[-1], "date") else str(features.index[-1]),
        composite=round(composite, 1),
        classification=classify(composite, settings.signal),
        trend_score=round(trend * 100, 1),
        moneyflow_score=round(mflow * 100, 1),
        confirming_categories=confirming,
        volume_confirmed=volume_confirmed,
        above_ema_trend=above_ema,
        reasons=reasons,
        features=feats,
    )
