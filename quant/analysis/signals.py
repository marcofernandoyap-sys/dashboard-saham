"""
Signal logic: BUY / TAKE PROFIT / STOP LOSS + position sizing.

Prinsip risk-first (non-negotiable):
  - BUY butuh konfirmasi >=2 kategori indikator (trend + money flow), TIDAK
    boleh dari 1 indikator saja.
  - Setiap sinyal BUY WAJIB menghasilkan stop loss (berbasis ATR).
  - Take profit berbasis risk-reward ratio (min 1:2), BUKAN target harian.
  - Position sizing dihitung dari jarak ke stop loss & risiko max per trade.

Modul ini murni menghitung PROPOSAL sinyal; eksekusi ditangani modul lain
(fase berikutnya). Untuk IDX default-nya jadi alert/semi-manual.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from quant.analysis.scoring import Score
from quant.config import SETTINGS


@dataclass
class TradePlan:
    ticker: str
    date: str
    action: str                 # "BUY" atau "NO_TRADE"
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    shares: int                 # jumlah saham (IDX: bulatkan ke lot=100 di layer eksekusi)
    risk_amount: float          # nominal risiko (modal * risk_pct)
    rationale: list[str]
    blocked_reasons: list[str]  # kenapa TIDAK BUY jika action == NO_TRADE


def atr_stop_loss(entry: float, atr: float,
                  mult: float = None) -> float:
    mult = mult if mult is not None else SETTINGS.signal.atr_stop_mult
    return entry - mult * atr


def take_profit_from_rr(entry: float, stop: float,
                        rr: float = None) -> float:
    rr = rr if rr is not None else SETTINGS.signal.min_risk_reward
    risk = entry - stop
    return entry + rr * risk


def position_size(capital: float, entry: float, stop: float,
                  risk_pct: float) -> tuple[int, float]:
    """
    Hitung jumlah saham agar kerugian di stop loss = capital * risk_pct.
    Return (shares, risk_amount). Aman terhadap pembagi nol.
    """
    risk_amount = capital * risk_pct
    per_share_risk = entry - stop
    if per_share_risk <= 0:
        return 0, 0.0
    shares = math.floor(risk_amount / per_share_risk)
    return max(shares, 0), risk_amount


def evaluate_buy(score: Score, capital: float,
                 is_speculative: bool = False,
                 settings=SETTINGS) -> TradePlan:
    """
    Terapkan aturan BUY watchlist utama (atau tier spekulatif jika ditandai).
    Menghasilkan TradePlan lengkap dengan stop loss & sizing, atau NO_TRADE
    beserta alasan penolakan.
    """
    s = settings.signal
    r = settings.risk
    blocked: list[str] = []

    entry = score.features.get("close")
    atr = score.features.get("atr")

    # --- Gerbang konfirmasi (semua wajib lolos) ---
    if score.composite < s.buy_score_threshold:
        blocked.append(
            f"skor komposit {score.composite} < ambang buy {s.buy_score_threshold}"
        )
    if score.confirming_categories < s.min_indicator_categories:
        blocked.append(
            f"hanya {score.confirming_categories} kategori indikator konfirmasi "
            f"(butuh >= {s.min_indicator_categories}: trend + money flow)"
        )
    if not score.volume_confirmed:
        blocked.append(
            f"volume belum konfirmasi (butuh >= {s.volume_confirm_mult}x rata-rata)"
        )
    if not score.above_ema_trend:
        blocked.append(f"harga belum di atas EMA{s.require_above_ema} (tren belum naik)")
    if entry is None or atr is None or atr <= 0:
        blocked.append("data harga/ATR tidak memadai untuk hitung stop loss")

    if blocked:
        return TradePlan(
            ticker=score.ticker, date=score.date, action="NO_TRADE",
            entry=entry or 0.0, stop_loss=0.0, take_profit=0.0,
            risk_reward=0.0, shares=0, risk_amount=0.0,
            rationale=list(score.reasons), blocked_reasons=blocked,
        )

    # --- Lolos: susun rencana dengan stop loss WAJIB ---
    risk_pct = (r.spec_risk_per_trade_pct if is_speculative
                else r.max_risk_per_trade_pct)
    stop = atr_stop_loss(entry, atr, s.atr_stop_mult)
    take_profit = take_profit_from_rr(entry, stop, s.min_risk_reward)
    rr = (take_profit - entry) / (entry - stop) if entry > stop else 0.0
    shares, risk_amount = position_size(capital, entry, stop, risk_pct)

    rationale = list(score.reasons)
    rationale.append(
        f"BUY: skor {score.composite}, {score.confirming_categories} kategori "
        f"konfirmasi, volume OK, di atas EMA{s.require_above_ema}. "
        f"Stop {stop:.2f} (ATR x{s.atr_stop_mult}), TP {take_profit:.2f} (RR 1:{rr:.1f}), "
        f"sizing {shares} saham @ risiko {risk_pct*100:.2f}% modal."
    )
    if is_speculative:
        rationale.insert(0, f"[{settings.speculative.warning_label}]")

    return TradePlan(
        ticker=score.ticker, date=score.date, action="BUY",
        entry=round(entry, 2), stop_loss=round(stop, 2),
        take_profit=round(take_profit, 2), risk_reward=round(rr, 2),
        shares=shares, risk_amount=round(risk_amount, 2),
        rationale=rationale, blocked_reasons=[],
    )
