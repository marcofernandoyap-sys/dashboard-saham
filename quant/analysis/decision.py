"""
Lapisan KEPUTUSAN per-saham untuk trade plan (swing).

Tujuan: mengubah output analisa (Score + TradePlan + status gerbang) menjadi
SATU verdikt yang mudah dibaca manusia — BUY / WATCH / AVOID — plus konteks
keputusan (jarak ke stop/TP, perkiraan lama tahan, R, dsb.).

PRINSIP:
  - Lapisan ini TIDAK mengubah strategi/edge yang sudah divalidasi. Ia hanya
    MERANGKUM gerbang yang sudah ada (skor, konfirmasi, volume, EMA, regime, RS,
    likuiditas, spekulatif) jadi keputusan display. Satu sumber kebenaran:
    CLI & dashboard memakai fungsi yang sama.
  - Tidak ada eksekusi order di sini. Murni analisa & penyajian.

Verdikt:
  BUY   : lolos SEMUA gerbang sinyal DAN regime bullish DAN RS>indeks DAN likuid.
  WATCH : setup valid (lolos gerbang sinyal & likuid) tapi diblokir regime/RS
          (konteks pasar/relatif belum sejajar) — pantau, jangan entry.
  AVOID : gagal gerbang sinyal, atau tidak likuid (tak bisa dieksekusi wajar).

Flag spekulatif ('gorengan') ditandai TERPISAH; ia tidak menaikkan verdikt dan
memberi peringatan risiko meski verdikt BUY.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from quant.analysis.scoring import Score
from quant.analysis.signals import TradePlan


@dataclass
class Decision:
    ticker: str
    verdict: str                       # "BUY" | "WATCH" | "AVOID"
    reasons: list[str] = field(default_factory=list)
    speculative: bool = False
    liquid: bool = True
    # Konteks keputusan (None kalau tak ada rencana valid / bukan kandidat).
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    downside_pct: float | None = None   # % dari entry ke stop (risiko harga)
    upside_pct: float | None = None     # % dari entry ke TP (imbalan harga)
    risk_reward: float | None = None
    est_holding_days: int | None = None  # perkiraan kasar (ATR-based) sampai TP
    shares: int | None = None
    risk_amount: float | None = None


def _est_holding_days(entry: float | None, take_profit: float | None,
                      atr: float | None) -> int | None:
    """
    Perkiraan KASAR lama tahan sampai TP: jarak harga ke TP dibagi laju harian
    tipikal (ATR). Bukan ramalan — sekadar konteks 'ini swing berapa lama-an'.
    """
    if not entry or not take_profit or not atr or atr <= 0:
        return None
    days = math.ceil((take_profit - entry) / atr)
    return max(days, 1)


def decide(score: Score, plan: TradePlan, *,
           regime_ok: bool | None, rs_ok: bool | None,
           liquid: bool = True, speculative: bool = False) -> Decision:
    """
    Rangkum jadi verdikt + konteks. `regime_ok`/`rs_ok` = status HARI INI
    (None diperlakukan sebagai 'tidak diketahui' -> tidak dianggap lolos).
    """
    spec = bool(speculative)

    # Tidak likuid -> AVOID mutlak (stop/TP tak bisa dieksekusi wajar).
    if not liquid:
        return Decision(ticker=score.ticker, verdict="AVOID", liquid=False,
                        speculative=spec,
                        reasons=["tidak likuid — eksekusi stop/TP tak wajar"])

    # Gagal gerbang sinyal -> AVOID (pakai alasan dari plan).
    if plan.action != "BUY":
        return Decision(ticker=score.ticker, verdict="AVOID", liquid=True,
                        speculative=spec,
                        reasons=list(plan.blocked_reasons) or ["gerbang sinyal belum lolos"])

    # Lolos gerbang sinyal & likuid -> hitung konteks.
    entry, stop, tp = plan.entry, plan.stop_loss, plan.take_profit
    downside = ((entry - stop) / entry * 100.0) if entry else None
    upside = ((tp - entry) / entry * 100.0) if entry else None
    hold = _est_holding_days(entry, tp, score.features.get("atr"))

    gates_ok = bool(regime_ok) and bool(rs_ok)
    if gates_ok:
        verdict = "BUY"
        reasons = [f"lolos semua gerbang (skor {score.composite}, RR 1:{plan.risk_reward})"]
        if spec:
            reasons.append("⚠ karakter spekulatif — pakai sizing/porsi lebih kecil")
    else:
        verdict = "WATCH"
        blk = []
        if not regime_ok:
            blk.append("regime pasar belum bullish")
        if not rs_ok:
            blk.append("kekuatan relatif < indeks")
        reasons = ["setup valid tapi diblokir: " + ", ".join(blk)]

    return Decision(
        ticker=score.ticker, verdict=verdict, reasons=reasons,
        speculative=spec, liquid=True,
        entry=entry, stop_loss=stop, take_profit=tp,
        downside_pct=round(downside, 2) if downside is not None else None,
        upside_pct=round(upside, 2) if upside is not None else None,
        risk_reward=plan.risk_reward, est_holding_days=hold,
        shares=plan.shares, risk_amount=plan.risk_amount,
    )
