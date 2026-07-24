#!/usr/bin/env python3
"""
CLI eksekusi: ubah sinyal actionable harian menjadi order lewat ExecutionEngine.

DEFAULT AMAN:
    python -m scripts.trade                     # PAPER (simulasi) broker lokal
    python -m scripts.trade --broker alpaca     # PAPER Alpaca US (butuh kredensial)
    python -m scripts.trade --status            # tampilkan gerbang + akun, tak order

Jalur LIVE (uang riil) SENGAJA di-gate keras:
    python -m scripts.trade --live              # tetap DITOLAK selama gerbang
                                                # backtest/paper belum lolos.

Ini mengeksekusi HANYA sinyal yang lolos SEMUA gerbang (signal+regime+RS),
konsisten dengan digest/backtest. Tidak ada logika sinyal baru di sini.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.analysis.signals import TradePlan
from quant.backtest.registry import live_readiness
from quant.config import SETTINGS
from quant.data.storage import Storage
from quant.execution.broker import BrokerError, LiveBrokerNotConfigured
from quant.execution.engine import ExecutionEngine
from quant.execution.journal import Journal, paper_readiness
from quant.execution.paper import PaperBroker
from quant.notify.digest import build_daily_report


def _build_broker(name: str, live: bool, capital: float):
    name = name.lower()
    if name == "paper":
        return PaperBroker(initial_cash=capital), True
    if name == "alpaca":
        from quant.execution.alpaca import from_env
        from quant.notify.channels import load_dotenv
        load_dotenv()
        return from_env(paper=not live), False        # US, bukan IDX
    if name == "idx":
        return LiveBrokerNotConfigured(), True          # placeholder, menolak
    raise SystemExit(f"broker tak dikenal: {name!r} (pilih: paper|alpaca|idx)")


def _print_gates(engine: ExecutionEngine) -> None:
    print("Gerbang kesiapan LIVE:")
    ready = live_readiness(SETTINGS)
    pr = paper_readiness(engine.journal, SETTINGS)
    print(f"  backtest : {'LOLOS' if ready['allowed'] else 'DIBLOKIR'}")
    for b in ready["blockers"]:
        print(f"     - {b}")
    print(f"  paper    : {'LOLOS' if pr['allowed'] else 'DIBLOKIR'} "
          f"({pr['stats']['n_days']} hari, {pr['stats']['n_trades']} trade)")
    for b in pr["blockers"]:
        print(f"     - {b}")
    blockers = engine.live_blockers()
    print(f"  => order LIVE saat ini: "
          f"{'DIIZINKAN' if not blockers else 'DITOLAK'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Eksekusi sinyal (paper default)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--broker", default=None, help="paper | alpaca | idx")
    ap.add_argument("--live", action="store_true",
                    help="minta jalur uang-riil (tetap di-gate keras)")
    ap.add_argument("--status", action="store_true",
                    help="tampilkan gerbang & akun saja, tanpa mengirim order")
    args = ap.parse_args()

    broker_name = (args.broker or SETTINGS.execution.broker).lower()
    try:
        broker, is_idx = _build_broker(broker_name, args.live, args.capital)
    except BrokerError as e:
        print(f"[trade] gagal siapkan broker: {e}")
        return 2

    settings = SETTINGS
    if args.live:
        settings = replace(SETTINGS, execution=replace(SETTINGS.execution,
                                                        mode="live"))
    engine = ExecutionEngine(broker, settings, Journal(), is_idx=is_idx)

    print("=" * 64)
    print(f"EKSEKUSI ({engine.mode.upper()}) via broker '{broker.name}' — "
          + SETTINGS.disclaimer)
    print("=" * 64)
    _print_gates(engine)

    if args.status:
        return 0

    storage = Storage()
    tickers = storage.tickers(market="IDX")
    if not tickers:
        print("Belum ada data. Jalankan: python -m scripts.ingest --index LQ45")
        return 1
    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None

    report = build_daily_report(ohlcv, index_df, args.capital, SETTINGS)
    if not report.actionable:
        print("\nTidak ada sinyal actionable hari ini -> tidak ada order. "
              "(Normal & sehat.)")
        return 0

    print(f"\nMengeksekusi {len(report.actionable)} sinyal actionable:")
    for a in report.actionable:
        plan = TradePlan(
            ticker=a.ticker, date=report.date, action="BUY", entry=a.entry,
            stop_loss=a.stop_loss, take_profit=a.take_profit,
            risk_reward=a.risk_reward, shares=a.shares,
            risk_amount=a.risk_amount, rationale=[], blocked_reasons=[],
        )
        try:
            decision = engine.execute_plan(plan)
        except BrokerError as e:
            print(f"  {a.ticker}: ERROR broker -> {e}")
            continue
        if decision.submitted and decision.fill:
            f = decision.fill
            print(f"  {a.ticker}: FILLED {f.qty} @ {f.price:,.2f} "
                  f"fee {f.fee:,.0f} ({engine.mode})")
        else:
            print(f"  {a.ticker}: TIDAK dieksekusi -> "
                  + "; ".join(decision.blockers or [decision.note]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
