#!/usr/bin/env python3
"""
Uji layer eksekusi — TANPA network (Alpaca tak dipanggil).

Fokus prinsip risk-first:
  - PaperBroker mengisi/menolak dgn benar & menjaga kas/posisi.
  - Engine membulatkan lot IDX ke bawah.
  - Gerbang LIVE MENOLAK order uang-riil selama backtest/paper belum lolos.
  - Broker IDX live (placeholder) menolak semua operasi.

Jalankan: python -m tests.test_execution
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.analysis.signals import TradePlan
from quant.config import SETTINGS
from quant.execution.broker import (BrokerError, LiveBrokerNotConfigured,
                                    Order)
from quant.execution.engine import ExecutionEngine
from quant.execution.journal import Journal, paper_readiness
from quant.execution.paper import PaperBroker


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        raise AssertionError(msg)


def _tmp(name: str) -> Path:
    d = tempfile.mkdtemp()
    return Path(d) / name


def test_paper_fill_and_reject() -> None:
    print("\n== PaperBroker isi & tolak ==")
    b = PaperBroker(initial_cash=1_000_000, fee_bps=20.0,
                    state_path=_tmp("acct.json"))
    f = b.submit(Order("AAA.JK", "buy", 100, ref_price=1000))
    check(f.ok and f.qty == 100, "buy 100 @1000 terisi")
    check(abs(f.fee - 100_000 * 0.002) < 1e-6, "fee 20bps benar")
    acct = b.account()
    check(acct.cash < 1_000_000, "kas berkurang setelah beli")
    check(len(acct.positions) == 1, "1 posisi terbuka")
    # jual lebih dari yang dimiliki -> ditolak
    r = b.submit(Order("AAA.JK", "sell", 200, ref_price=1100))
    check(not r.ok and "posisi tak cukup" in r.reason, "jual berlebih ditolak")
    # jual pas -> posisi tutup, kas naik
    s = b.submit(Order("AAA.JK", "sell", 100, ref_price=1100))
    check(s.ok, "jual 100 @1100 terisi")
    check(len(b.positions()) == 0, "posisi tertutup")


def test_insufficient_cash_rejected() -> None:
    print("\n== Kas kurang -> tolak ==")
    b = PaperBroker(initial_cash=50_000, fee_bps=20.0, state_path=_tmp("a.json"))
    f = b.submit(Order("BBB.JK", "buy", 100, ref_price=1000))  # butuh ~100k
    check(not f.ok and "kas tak cukup" in f.reason, "beli di atas kas ditolak")


def test_lot_rounding_idx() -> None:
    print("\n== Pembulatan lot IDX (100) ==")
    b = PaperBroker(initial_cash=10_000_000, state_path=_tmp("a.json"))
    eng = ExecutionEngine(b, SETTINGS, Journal(_tmp("j.db")), is_idx=True)
    d = eng.submit_order(Order("CCC.JK", "buy", 250, ref_price=1000))
    check(d.submitted and d.fill.qty == 200, "250 -> dibulatkan ke 200 (2 lot)")
    d2 = eng.submit_order(Order("DDD.JK", "buy", 50, ref_price=1000))
    check(not d2.submitted, "50 (<1 lot) -> tidak dieksekusi")


def test_live_gate_refuses() -> None:
    print("\n== Gerbang LIVE menolak uang-riil ==")
    b = PaperBroker(initial_cash=10_000_000, state_path=_tmp("a.json"))
    # Paksa mode live via config; allow_live tetap default False.
    live_settings = replace(SETTINGS, execution=replace(SETTINGS.execution,
                                                        mode="live"))
    eng = ExecutionEngine(b, live_settings, Journal(_tmp("j.db")), is_idx=True)
    check(eng.mode == "live", "engine mendeteksi mode live")
    blockers = eng.live_blockers()
    check(len(blockers) > 0, "ada blocker (allow_live False / gerbang)")
    d = eng.execute_plan(TradePlan(
        ticker="EEE.JK", date="2026-01-01", action="BUY", entry=1000,
        stop_loss=900, take_profit=1300, risk_reward=3.0, shares=100,
        risk_amount=10000, rationale=[], blocked_reasons=[]))
    check(not d.submitted, "order LIVE DITOLAK gerbang")
    # broker tak menerima apa pun -> tak ada posisi
    check(len(b.positions()) == 0, "tak ada posisi terbentuk saat live ditolak")


def test_live_gate_allow_live_still_blocked_by_track_record() -> None:
    print("\n== allow_live=True tetap butuh backtest+paper ==")
    b = PaperBroker(initial_cash=10_000_000, state_path=_tmp("a.json"))
    st = replace(SETTINGS, execution=replace(SETTINGS.execution,
                                             mode="live", allow_live=True))
    eng = ExecutionEngine(b, st, Journal(_tmp("j.db")), is_idx=True)
    blockers = eng.live_blockers()
    # allow_live True dihapus dari blocker, tapi paper track record (0 hari/trade)
    # dan/atau backtest tetap memblokir.
    check(all("allow_live" not in x for x in blockers),
          "allow_live bukan lagi blocker")
    check(any("paper" in x for x in blockers), "rekam jejak paper masih memblokir")


def test_paper_mode_not_gated() -> None:
    print("\n== Mode paper tidak di-gate ==")
    b = PaperBroker(initial_cash=10_000_000, state_path=_tmp("a.json"))
    eng = ExecutionEngine(b, SETTINGS, Journal(_tmp("j.db")), is_idx=True)
    check(eng.mode == "paper", "default mode paper")
    d = eng.execute_plan(TradePlan(
        ticker="FFF.JK", date="2026-01-01", action="BUY", entry=1000,
        stop_loss=900, take_profit=1300, risk_reward=3.0, shares=100,
        risk_amount=10000, rationale=[], blocked_reasons=[]))
    check(d.submitted and d.fill.ok, "order paper diteruskan tanpa gerbang live")


def test_journal_feeds_paper_readiness() -> None:
    print("\n== Journal -> paper_readiness ==")
    j = Journal(_tmp("j.db"))
    b = PaperBroker(initial_cash=10_000_000, state_path=_tmp("a.json"))
    eng = ExecutionEngine(b, SETTINGS, j, is_idx=True)
    eng.submit_order(Order("GGG.JK", "buy", 100, ref_price=1000))
    stats = j.paper_stats()
    check(stats["n_trades"] >= 1, "fill tercatat di journal")
    pr = paper_readiness(j, SETTINGS)
    check(not pr["allowed"], "1 trade < minimum -> paper belum siap (benar)")


def test_idx_live_placeholder_refuses() -> None:
    print("\n== Broker IDX live (placeholder) menolak ==")
    b = LiveBrokerNotConfigured()
    check(b.is_live, "placeholder ditandai live")
    raised = False
    try:
        b.submit(Order("HHH.JK", "buy", 100, ref_price=1000))
    except BrokerError:
        raised = True
    check(raised, "submit ke broker IDX live -> BrokerError (menolak)")


if __name__ == "__main__":
    print("Menjalankan uji eksekusi...")
    test_paper_fill_and_reject()
    test_insufficient_cash_rejected()
    test_lot_rounding_idx()
    test_live_gate_refuses()
    test_live_gate_allow_live_still_blocked_by_track_record()
    test_paper_mode_not_gated()
    test_journal_feeds_paper_readiness()
    test_idx_live_placeholder_refuses()
    print("\nSemua uji eksekusi LULUS.\n")
