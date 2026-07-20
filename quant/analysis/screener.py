"""
Seleksi watchlist otomatis.

Proses (sesuai spec):
  1. Screening likuiditas dari universe (LQ45/IDX30) — buang saham tak likuid.
  2. Jalankan Analysis Engine (scoring) ke semua yang lolos.
  3. Watchlist final = skor komposit tertinggi (top N), di-refresh berkala.
  4. User boleh override manual (ditangani di layer pemanggil/dashboard).

Juga: deteksi tier SPEKULATIF ("gorengan") — TERPISAH dari watchlist utama,
diberi label peringatan. Sinyal di tier ini TIDAK dianggap setara dengan
sinyal saham likuid (lihat peringatan di config & label warning).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.analysis.scoring import Score, compute_features, score_ticker
from quant.config import SETTINGS


@dataclass
class LiquidityStat:
    ticker: str
    avg_volume: float
    avg_value: float          # rata-rata nilai transaksi harian (close*volume)
    last_close: float
    passed: bool
    reason: str


def screen_liquidity_idx(features_by_ticker: dict[str, pd.DataFrame],
                         settings=SETTINGS) -> list[LiquidityStat]:
    """
    Filter likuiditas IDX. `features_by_ticker` = {ticker: df OHLCV/fitur}.
    Free float TIDAK tersedia dari yfinance -> dicatat sebagai caveat, universe
    LQ45/IDX30 sudah menjaga syarat free float secara tidak langsung.
    """
    sp = settings.screen_idx
    out: list[LiquidityStat] = []
    for ticker, df in features_by_ticker.items():
        if df is None or df.empty or len(df) < 20:
            out.append(LiquidityStat(ticker, 0, 0, 0, False, "data tidak cukup"))
            continue
        recent = df.tail(20)
        avg_vol = float(recent["volume"].mean())
        avg_val = float((recent["close"] * recent["volume"]).mean())
        last_close = float(df["close"].iloc[-1])

        ok_vol = avg_vol >= sp.min_avg_daily_volume
        ok_val = avg_val >= sp.min_avg_daily_value_idr
        passed = ok_vol or ok_val   # lolos jika salah satu kriteria likuiditas
        reason = (
            f"avg_vol={avg_vol:,.0f} (min {sp.min_avg_daily_volume:,}), "
            f"avg_val=Rp{avg_val/1e9:.1f}M (min Rp{sp.min_avg_daily_value_idr/1e9:.0f}M)"
        )
        out.append(LiquidityStat(ticker, avg_vol, avg_val, last_close,
                                 passed, reason))
    return out


def build_watchlist(ohlcv_by_ticker: dict[str, pd.DataFrame],
                    settings=SETTINGS) -> tuple[list[Score], list[LiquidityStat]]:
    """
    Kembalikan (watchlist terurut skor desc, statistik likuiditas).
    Hanya ticker yang lolos likuiditas yang di-skor.
    """
    features = {t: compute_features(df) for t, df in ohlcv_by_ticker.items()
                if df is not None and not df.empty}
    liq = screen_liquidity_idx(features, settings)
    passed = {s.ticker for s in liq if s.passed}

    scores: list[Score] = []
    for ticker in passed:
        sc = score_ticker(ticker, features[ticker], settings)
        if sc is not None:
            scores.append(sc)

    scores.sort(key=lambda s: s.composite, reverse=True)
    return scores[: settings.screen_idx.watchlist_top_n], liq


def detect_speculative(df: pd.DataFrame, settings=SETTINGS) -> dict | None:
    """
    Deteksi karakter tier spekulatif pada bar terakhir. Return dict flag jika
    ADA pemicu, else None. Ini BUKAN sinyal 'aman' — hanya penanda risiko tinggi.
    """
    if df is None or len(df) < 21:
        return None
    sd = settings.speculative
    recent = df.iloc[-1]
    prev = df.iloc[-2]
    vol_avg = df["volume"].iloc[-21:-1].mean()

    triggers: list[str] = []
    vol_ratio = float(recent["volume"] / vol_avg) if vol_avg else 0.0
    if vol_ratio > sd.volume_spike_mult:
        triggers.append(f"volume {vol_ratio:.1f}x rata-rata (>{sd.volume_spike_mult}x)")

    gap = (recent["open"] - prev["close"]) / prev["close"] if prev["close"] else 0.0
    if abs(gap) > sd.open_gap_pct:
        triggers.append(f"gap pembukaan {gap*100:+.1f}% (>{sd.open_gap_pct*100:.0f}%)")

    if not triggers:
        return None
    return {
        "ticker": recent.get("ticker"),
        "warning": sd.warning_label,
        "triggers": triggers,
        "vol_ratio": vol_ratio,
        "gap_pct": gap * 100,
    }
