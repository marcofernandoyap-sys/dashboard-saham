#!/usr/bin/env python3
"""
CLI: workflow paper trading HARIAN — untuk membangun rekam-jejak paper (Fase 5b).

Setiap hari trading (Sen-Jum), skrip ini:
  1. Refresh data harga IDX terakhir (period pendek, murah).
  2. Bangun laporan sinyal actionable hari ini.
  3. Kirim ke PaperBroker lokal (uang-VIRTUAL, bukan uang riil).
  4. Rekam ke journal (`exec_fills`) supaya gate paper_readiness terisi.
  5. Log semua ke `data/paper/YYYY-MM-DD.log` untuk audit.

MENGAPA butuh ini: gate live memblokir sampai `min_paper_trading_days=60` &
`min_recorded_trades=30` terpenuhi — ini butuh waktu KALENDER, bukan compute.
Makin cepat mulai, makin cepat gate paper lolos. Backtest gate terpisah &
tetap harus lolos sendiri (Sharpe >= 1.0, dst.)

Aman untuk cron: exit code 0 kalau sukses (termasuk "tidak ada sinyal" — normal).
Exit code 1 hanya kalau ingest gagal total. Cocok dijadwalkan tiap sore setelah
close IDX (~17:00 WIB).

Contoh manual:
    python -m scripts.paper_daily
    python -m scripts.paper_daily --capital 100000000 --skip-ingest

Contoh cron (macOS/Linux, tiap hari kerja 17:15 WIB / 10:15 UTC):
    15 10 * * 1-5 cd /Users/crm/quant-trading && \\
        .venv/bin/python -m scripts.paper_daily >> data/paper/cron.log 2>&1
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.analysis.signals import TradePlan
from quant.backtest.registry import live_readiness
from quant.config import SETTINGS
from quant.data.ingestion import YFinanceProvider, ingest
from quant.data.storage import Storage
from quant.execution.broker import BrokerError
from quant.execution.engine import ExecutionEngine
from quant.execution.journal import Journal, paper_readiness
from quant.execution.paper import PaperBroker
from quant.notify.digest import build_daily_report
from quant.universe import idx_tickers


class _Tee:
    """Tulis stream ke stdout DAN file — supaya cron log rapi & juga terlihat."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def write(self, s: str) -> None:
        sys.__stdout__.write(s)
        self._f.write(s)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def _is_trading_day(d: datetime) -> bool:
    """IDX libur Sabtu-Minggu. (Libur nasional tidak dicek — cron cukup skip weekend.)"""
    return d.weekday() < 5


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper trading harian (bangun rekam jejak)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--index", default="LQ45", help="LQ45 atau IDX30")
    ap.add_argument("--period", default="1mo", help="periode refresh yfinance")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="lewati refresh data (pakai storage yg ada)")
    ap.add_argument("--force", action="store_true",
                    help="jalankan meski hari ini bukan hari kerja")
    args = ap.parse_args()

    now = datetime.now()
    log_dir = Path("data/paper")
    log_path = log_dir / f"{now.date()}.log"
    tee = _Tee(log_path)
    sys.stdout = tee

    try:
        print("=" * 72)
        print(f"PAPER DAILY  {now:%Y-%m-%d %H:%M:%S}  — {SETTINGS.disclaimer}")
        print("=" * 72)

        if not _is_trading_day(now) and not args.force:
            print(f"Hari ini {now:%A} — IDX libur. Lewati. (Gunakan --force untuk paksa.)")
            return 0

        # (1) Refresh data harga IDX
        storage = Storage()
        if not args.skip_ingest:
            tickers = idx_tickers(args.index)
            print(f"[1/4] Refresh data {len(tickers)} ticker (period={args.period})...")
            try:
                provider = YFinanceProvider(market="IDX")
                results = ingest(tickers, provider, storage,
                                 period=args.period, interval="1d")
                ok = sum(1 for v in results.values() if v > 0)
                fail = sum(1 for v in results.values() if v < 0)
                print(f"   {ok} sukses, {fail} gagal.")
                if fail == len(tickers):
                    print("   Semua gagal — mungkin masalah network. Berhenti.")
                    return 1
            except Exception as e:
                print(f"   Ingest gagal: {e}. Coba pakai data lama.")
        else:
            print("[1/4] --skip-ingest -> pakai data storage yg ada.")

        # (2) Broker paper + gerbang status
        broker = PaperBroker(initial_cash=args.capital)
        journal = Journal()
        engine = ExecutionEngine(broker, SETTINGS, journal, is_idx=True)

        print("[2/4] Gerbang kesiapan LIVE (INFO — paper tidak butuh ini lolos):")
        ready = live_readiness(SETTINGS)
        pr = paper_readiness(journal, SETTINGS)
        print(f"     backtest : {'LOLOS' if ready['allowed'] else 'DIBLOKIR'}")
        print(f"     paper    : {'LOLOS' if pr['allowed'] else 'DIBLOKIR'} "
              f"({pr['stats']['n_days']} hari, {pr['stats']['n_trades']} trade)")

        # (3) Bangun sinyal actionable
        idx_ticks = storage.tickers(market="IDX")
        if not idx_ticks:
            print("[3/4] Belum ada data. Jalankan: python -m scripts.ingest --index LQ45")
            return 1
        ohlcv = {t: storage.load_ohlcv(t) for t in idx_ticks}
        index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
        index_df = index_df if not index_df.empty else None
        report = build_daily_report(ohlcv, index_df, args.capital, SETTINGS)

        # (4) Eksekusi
        if not report.actionable:
            print("[3/4] Tidak ada sinyal actionable — tidak ada order. (Normal & sehat.)")
            print("[4/4] Selesai (paper journal tidak berubah hari ini).")
            return 0
        print(f"[3/4] {len(report.actionable)} sinyal actionable ditemukan.")
        print("[4/4] Kirim ke PaperBroker (uang virtual):")
        filled, skipped = 0, 0
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
                print(f"   {a.ticker}: ERROR broker -> {e}")
                skipped += 1
                continue
            if decision.submitted and decision.fill:
                f = decision.fill
                print(f"   {a.ticker}: FILLED {f.qty} @ {f.price:,.2f} "
                      f"fee {f.fee:,.0f} (paper)")
                filled += 1
            else:
                print(f"   {a.ticker}: SKIP -> "
                      + "; ".join(decision.blockers or [decision.note]))
                skipped += 1
        print(f"Rekap: {filled} filled, {skipped} skip.")

        # Update gate paper setelah hari ini
        pr2 = paper_readiness(journal, SETTINGS)
        print(f"Paper journal sekarang: {pr2['stats']['n_days']} hari, "
              f"{pr2['stats']['n_trades']} trade.")
        return 0
    finally:
        sys.stdout = sys.__stdout__
        tee.close()


if __name__ == "__main__":
    raise SystemExit(main())
