"""
Jejak akumulasi/distribusi — PROKSI "bandarmology" dari harga+volume.

PENTING — BATAS KEJUJURAN:
  Ini BUKAN deteksi bandar sungguhan. Data broker summary (net beli/jual per
  kode broker, asing vs lokal, done-by-broker) TIDAK tersedia dari yfinance —
  itu satu-satunya sumber yang benar-benar menunjukkan "siapa yang pegang".
  Modul ini hanya membaca JEJAK harga/volume yang DITINGGALKAN pemegang besar:
    - A/D line & OBV naik saat harga flat/turun  -> ada yang menyerap supply
      (indikasi akumulasi diam-diam / "bandar masih kumpulin").
    - Harga naik tapi A/D & OBV turun            -> ada yang melepas ke kekuatan
      (indikasi distribusi / "bandar mulai keluar").
  Jejak ini bisa juga sekadar ritel ramai-ramai. Perlakukan sebagai KONTEKS,
  BUKAN bukti, dan JANGAN dipakai sebagai gerbang BUY sebelum lolos walk-forward.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CAVEAT = ("PROKSI dari harga/volume — BUKAN bukti keberadaan bandar. "
          "Deteksi sungguhan butuh data broker summary (net beli/jual per broker). "
          "Bisa juga sekadar ritel; jangan jadikan gerbang beli.")


@dataclass
class Footprint:
    ticker: str
    verdict: str                    # "AKUMULASI" | "DISTRIBUSI" | "NETRAL"
    confidence: str                 # "kuat" | "sedang" | "lemah"
    score: float                    # -1..+1 (net tekanan jejak)
    lookback: int
    price_change_pct: float
    ad_dir: int                     # arah A/D line: -1/0/+1
    obv_dir: int                    # arah OBV: -1/0/+1
    cmf: float                      # rata-rata CMF di window
    vol_updown_ratio: float         # Σvol hari-naik / Σvol hari-turun
    absorption: bool                # harga flat/turun TAPI net akumulasi (tell terkuat)
    signals: list[str] = field(default_factory=list)
    caveat: str = CAVEAT


def _dir(delta: float) -> int:
    if delta > 0:
        return 1
    if delta < 0:
        return -1
    return 0


def compute_footprint(features: pd.DataFrame, ticker: str = "",
                      lookback: int = 20) -> Footprint | None:
    """
    Baca jejak akumulasi/distribusi pada `lookback` bar terakhir.

    `features` = output compute_features (butuh kolom close, volume; memakai
    ad/obv/cmf bila ada). Return None kalau data kurang.
    """
    if features is None or len(features) < lookback + 1:
        return None

    win = features.tail(lookback)
    close = win["close"]
    first_close = float(close.iloc[0])
    last_close = float(close.iloc[-1])
    price_change_pct = ((last_close - first_close) / first_close * 100.0
                        if first_close else 0.0)

    contribs: list[float] = []
    signals: list[str] = []

    # A/D line: arah kumulatif sepanjang window.
    ad_dir = 0
    if "ad" in win and win["ad"].notna().any():
        ad_dir = _dir(float(win["ad"].iloc[-1]) - float(win["ad"].iloc[0]))
        contribs.append(float(ad_dir))

    # OBV: arah kumulatif sepanjang window.
    obv_dir = 0
    if "obv" in win and win["obv"].notna().any():
        obv_dir = _dir(float(win["obv"].iloc[-1]) - float(win["obv"].iloc[0]))
        contribs.append(float(obv_dir))

    # CMF: rata-rata tekanan money flow (sudah -1..+1).
    cmf_mean = 0.0
    if "cmf" in win and win["cmf"].notna().any():
        cmf_mean = float(win["cmf"].mean())
        contribs.append(float(np.clip(cmf_mean * 5.0, -1.0, 1.0)))

    # Volume hari-naik vs hari-turun (tekanan permintaan bersih).
    ret = close.diff()
    vol = win["volume"]
    up_vol = float(vol[ret > 0].sum())
    down_vol = float(vol[ret < 0].sum())
    vol_ratio = (up_vol / down_vol) if down_vol > 0 else (2.0 if up_vol > 0 else 1.0)
    contribs.append(float(np.clip(vol_ratio - 1.0, -1.0, 1.0)))

    score = float(np.mean(contribs)) if contribs else 0.0

    # Verdikt dari net score.
    if score >= 0.25:
        verdict = "AKUMULASI"
    elif score <= -0.25:
        verdict = "DISTRIBUSI"
    else:
        verdict = "NETRAL"

    # Kepercayaan: besar |score| + berapa komponen sepakat arahnya.
    sign = np.sign(score)
    agree = sum(1 for c in contribs if np.sign(c) == sign and c != 0)
    if abs(score) >= 0.6 and agree >= 3:
        confidence = "kuat"
    elif abs(score) >= 0.35:
        confidence = "sedang"
    else:
        confidence = "lemah"

    # Absorpsi: harga flat/turun tapi jejak akumulasi -> tell "bandar menyerap".
    absorption = price_change_pct <= 2.0 and score >= 0.35

    # Narasi.
    if ad_dir > 0:
        signals.append("A/D line menaik (ada yang menyerap supply)")
    elif ad_dir < 0:
        signals.append("A/D line menurun (ada yang melepas)")
    if obv_dir > 0:
        signals.append("OBV menaik (volume searah beli)")
    elif obv_dir < 0:
        signals.append("OBV menurun (volume searah jual)")
    if cmf_mean > 0.05:
        signals.append(f"CMF +{cmf_mean:.2f} (tekanan beli dominan)")
    elif cmf_mean < -0.05:
        signals.append(f"CMF {cmf_mean:.2f} (tekanan jual dominan)")
    signals.append(f"volume naik:turun = {vol_ratio:.2f}x "
                   f"({'lebih ramai saat naik' if vol_ratio > 1 else 'lebih ramai saat turun'})")
    if absorption:
        signals.append("⚑ ABSORPSI: harga flat/turun tapi jejak akumulasi — "
                       "indikasi kuat ada pemegang besar mengumpulkan diam-diam")
    if price_change_pct >= 5.0 and score <= -0.35:
        signals.append("⚑ DISTRIBUSI KE KEKUATAN: harga naik tapi jejak melepas — "
                       "indikasi pemegang besar mulai keluar")

    return Footprint(
        ticker=ticker, verdict=verdict, confidence=confidence,
        score=round(score, 3), lookback=lookback,
        price_change_pct=round(price_change_pct, 2),
        ad_dir=ad_dir, obv_dir=obv_dir, cmf=round(cmf_mean, 4),
        vol_updown_ratio=round(vol_ratio, 2), absorption=absorption,
        signals=signals,
    )
