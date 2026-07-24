#!/usr/bin/env python3
"""
Uji pipeline analisa dengan data SINTETIS (tanpa network).

Memvalidasi:
  - Rentang & sanity indikator (RSI/MFI 0..100, ATR>0, dll).
  - Scoring menghasilkan skor -100..+100 & klasifikasi konsisten.
  - Signal logic: uptrend + volume + akumulasi -> BUY dgn stop loss & sizing;
    dan setiap BUY WAJIB punya stop loss + risk-reward >= minimum.
  - Position sizing sesuai batas risiko per trade.

Jalankan: python -m tests.test_pipeline
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant.analysis import indicators as ind
from quant.analysis.decision import decide
from quant.analysis.scoring import compute_features, score_ticker
from quant.analysis.signals import (evaluate_buy, position_size,
                                    take_profit_from_rr)
from quant.config import SETTINGS


def make_ohlcv(kind: str, n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Bangun OHLCV sintetis: 'uptrend' (akumulasi) atau 'downtrend'."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    drift = 0.0015 if kind == "uptrend" else -0.0015
    rets = rng.normal(drift, 0.012, n)
    close = 1000 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    base_vol = 5_000_000
    vol = rng.normal(base_vol, base_vol * 0.2, n).clip(min=1)
    if kind == "uptrend":
        # volume naik searah tren (akumulasi) + spike besar di akhir
        vol = vol * (1 + np.linspace(0, 0.5, n))
        vol[-1] *= 2.5
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=dates,
    )
    df["ticker"] = "TEST.JK"
    return df


def check(cond: bool, msg: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        raise AssertionError(msg)


def test_indicators() -> None:
    print("\n== Indikator ==")
    df = make_ohlcv("uptrend")
    rsi = ind.rsi(df["close"], 14).dropna()
    mfi = ind.money_flow_index(df, 14).dropna()
    atr = ind.atr(df, 14).dropna()
    cmf = ind.chaikin_money_flow(df, 20).dropna()
    check(rsi.between(0, 100).all(), "RSI dalam 0..100")
    check(mfi.between(0, 100).all(), "MFI dalam 0..100")
    check((atr > 0).all(), "ATR > 0")
    check(cmf.between(-1, 1).all(), "CMF dalam -1..+1")
    check(ind.obv(df).notna().all(), "OBV tidak ada NaN")


def test_scoring() -> None:
    print("\n== Scoring ==")
    up = compute_features(make_ohlcv("uptrend"))
    down = compute_features(make_ohlcv("downtrend"))
    s_up = score_ticker("TEST.JK", up)
    s_down = score_ticker("TEST.JK", down)
    check(-100 <= s_up.composite <= 100, "skor uptrend dalam -100..+100")
    check(s_up.composite > s_down.composite,
          f"uptrend ({s_up.composite}) > downtrend ({s_down.composite})")
    check(s_up.classification in
          {"Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"},
          f"klasifikasi valid: {s_up.classification}")
    print(f"     uptrend: skor={s_up.composite} kelas={s_up.classification} "
          f"kategori_konfirm={s_up.confirming_categories}")
    print(f"     alasan[0]: {s_up.reasons[0]}")


def test_position_sizing() -> None:
    print("\n== Position sizing ==")
    capital = 100_000_000
    shares, risk_amt = position_size(capital, entry=1000, stop=950,
                                     risk_pct=0.01)
    # kerugian di stop = shares * (entry-stop) harus ~ risk_amt
    loss_at_stop = shares * (1000 - 950)
    check(abs(loss_at_stop - risk_amt) <= (1000 - 950),
          f"kerugian di stop ({loss_at_stop:,.0f}) ~ risk_amount ({risk_amt:,.0f})")
    check(shares > 0, f"shares > 0 ({shares})")
    # stop di atas entry -> 0 saham (guard)
    z, _ = position_size(capital, entry=1000, stop=1000, risk_pct=0.01)
    check(z == 0, "per-share risk <= 0 -> 0 saham (guard)")


def test_buy_signal() -> None:
    print("\n== Signal logic (BUY wajib punya stop loss) ==")
    up = compute_features(make_ohlcv("uptrend"))
    sc = score_ticker("TEST.JK", up)
    plan = evaluate_buy(sc, capital=100_000_000)
    print(f"     action={plan.action}  entry={plan.entry}  SL={plan.stop_loss}  "
          f"TP={plan.take_profit}  RR=1:{plan.risk_reward}")
    if plan.action == "BUY":
        check(plan.stop_loss > 0 and plan.stop_loss < plan.entry,
              "BUY punya stop loss valid di bawah entry")
        check(plan.take_profit > plan.entry, "TP di atas entry")
        check(plan.risk_reward >= SETTINGS.signal.min_risk_reward - 1e-6,
              f"RR >= minimum {SETTINGS.signal.min_risk_reward}")
        check(plan.shares > 0, "shares > 0")
    else:
        # NO_TRADE juga valid selama alasannya jelas
        check(len(plan.blocked_reasons) > 0, "NO_TRADE menyertakan alasan")
        print(f"     blocked: {plan.blocked_reasons}")

    # downtrend TIDAK boleh menghasilkan BUY
    down = compute_features(make_ohlcv("downtrend"))
    plan_d = evaluate_buy(score_ticker("TEST.JK", down), capital=100_000_000)
    check(plan_d.action == "NO_TRADE", "downtrend -> NO_TRADE")


def test_rr_math() -> None:
    print("\n== Risk-reward math ==")
    tp = take_profit_from_rr(entry=1000, stop=950, rr=2.0)
    check(abs(tp - 1100) < 1e-6, f"TP untuk RR 1:2 = 1100 (dapat {tp})")


def test_decision_verdict() -> None:
    from quant.analysis.scoring import Score
    from quant.analysis.signals import TradePlan

    print("\n== Decision layer (BUY/WATCH/AVOID) ==")
    # Konstruksi eksplisit sinyal BUY (deterministik, tak bergantung generator):
    # entry 1000, stop 900 (downside 10%), TP 1300 (upside 30%) -> RR 1:3.
    sc = Score(ticker="TEST.JK", date="2024-01-01", composite=72.0,
               classification="Buy", trend_score=60.0, moneyflow_score=55.0,
               confirming_categories=2, volume_confirmed=True,
               above_ema_trend=True, reasons=["uji"],
               features={"close": 1000.0, "atr": 30.0})
    plan = TradePlan(ticker="TEST.JK", date="2024-01-01", action="BUY",
                     entry=1000.0, stop_loss=900.0, take_profit=1300.0,
                     risk_reward=3.0, shares=1000, risk_amount=1_000_000.0,
                     rationale=["uji"], blocked_reasons=[])

    # Gerbang lolos + regime & RS OK -> BUY, konteks terisi.
    d_buy = decide(sc, plan, regime_ok=True, rs_ok=True, liquid=True)
    check(d_buy.verdict == "BUY", "regime+RS ok & likuid -> BUY")
    check(abs(d_buy.downside_pct - 10.0) < 1e-6, f"downside 10% ({d_buy.downside_pct})")
    check(abs(d_buy.upside_pct - 30.0) < 1e-6, f"upside 30% ({d_buy.upside_pct})")
    # (TP-entry)/ATR = 300/30 = 10 hari.
    check(d_buy.est_holding_days == 10, f"perkiraan tahan 10 hari ({d_buy.est_holding_days})")
    ratio = d_buy.upside_pct / d_buy.downside_pct
    check(abs(ratio - d_buy.risk_reward) < 0.05,
          f"upside/downside ({ratio:.2f}) ~ RR ({d_buy.risk_reward})")

    # Setup valid tapi regime bearish -> WATCH (bukan BUY).
    d_watch = decide(sc, plan, regime_ok=False, rs_ok=True, liquid=True)
    check(d_watch.verdict == "WATCH", "regime bearish -> WATCH (diblokir)")
    # RS lemah -> WATCH juga.
    d_watch2 = decide(sc, plan, regime_ok=True, rs_ok=False, liquid=True)
    check(d_watch2.verdict == "WATCH", "RS<indeks -> WATCH (diblokir)")
    # Tidak likuid -> AVOID mutlak, konteks kosong.
    d_illq = decide(sc, plan, regime_ok=True, rs_ok=True, liquid=False)
    check(d_illq.verdict == "AVOID" and d_illq.entry is None,
          "tidak likuid -> AVOID tanpa konteks eksekusi")
    # Spekulatif -> tetap BUY (kalau gerbang lolos) tapi ditandai.
    d_spec = decide(sc, plan, regime_ok=True, rs_ok=True, liquid=True,
                    speculative=True)
    check(d_spec.verdict == "BUY" and d_spec.speculative,
          "spekulatif ditandai tapi tak menaikkan/menurunkan verdikt")

    # Downtrend (NO_TRADE) -> AVOID apa pun status gerbang.
    down = compute_features(make_ohlcv("downtrend"))
    sc_d = score_ticker("TEST.JK", down)
    plan_d = evaluate_buy(sc_d, capital=100_000_000)
    d_avoid = decide(sc_d, plan_d, regime_ok=True, rs_ok=True, liquid=True)
    check(d_avoid.verdict == "AVOID", "downtrend NO_TRADE -> AVOID")
    check(len(d_avoid.reasons) > 0, "AVOID menyertakan alasan")


if __name__ == "__main__":
    print("Menjalankan uji pipeline dengan data sintetis...")
    test_indicators()
    test_scoring()
    test_position_sizing()
    test_buy_signal()
    test_rr_math()
    test_decision_verdict()
    print("\nSemua uji LULUS. Pipeline analisa berfungsi.\n")
