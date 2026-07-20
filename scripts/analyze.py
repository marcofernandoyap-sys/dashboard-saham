#!/usr/bin/env python3
"""
CLI: jalankan Analysis Engine atas data tersimpan -> watchlist + trade plan.

Contoh:
    python -m scripts.analyze --capital 100000000
    python -m scripts.analyze --capital 100000000 --top 15

Read-only: hanya menampilkan sinyal & rencana. TIDAK mengeksekusi order apa pun.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.analysis.screener import build_watchlist, detect_speculative
from quant.analysis.signals import evaluate_buy
from quant.config import SETTINGS
from quant.data.storage import Storage


def main() -> int:
    ap = argparse.ArgumentParser(description="Analisa & susun watchlist")
    ap.add_argument("--capital", type=float, required=True,
                    help="total modal (untuk position sizing)")
    ap.add_argument("--top", type=int, default=SETTINGS.screen_idx.watchlist_top_n)
    args = ap.parse_args()

    storage = Storage()
    tickers = storage.tickers()
    if not tickers:
        print("Belum ada data. Jalankan: python -m scripts.ingest --index LQ45")
        return 1

    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    watchlist, liq = build_watchlist(ohlcv, SETTINGS)

    print("\n" + "=" * 72)
    print(f"WATCHLIST (top {args.top}) — {SETTINGS.disclaimer}")
    print("=" * 72)
    print(f"{'Ticker':<10}{'Skor':>7}{'Trend':>7}{'MFlow':>7}  {'Klasifikasi':<12}{'Konfirm':>8}")
    print("-" * 72)
    for sc in watchlist[: args.top]:
        print(f"{sc.ticker:<10}{sc.composite:>7.1f}{sc.trend_score:>7.1f}"
              f"{sc.moneyflow_score:>7.1f}  {sc.classification:<12}"
              f"{sc.confirming_categories:>8}")

    print("\n" + "=" * 72)
    print("TRADE PLAN (kandidat BUY yang lolos semua gerbang risiko)")
    print("=" * 72)
    any_buy = False
    for sc in watchlist:
        spec = detect_speculative(ohlcv[sc.ticker], SETTINGS)
        plan = evaluate_buy(sc, args.capital, is_speculative=bool(spec))
        if plan.action == "BUY":
            any_buy = True
            print(f"\n[BUY] {plan.ticker}  entry={plan.entry}  SL={plan.stop_loss}  "
                  f"TP={plan.take_profit}  RR=1:{plan.risk_reward}  "
                  f"shares={plan.shares}  risiko=Rp{plan.risk_amount:,.0f}")
            for reason in plan.rationale:
                print(f"     - {reason}")
    if not any_buy:
        print("\nTidak ada kandidat BUY yang memenuhi semua konfirmasi hari ini.")
        print("(Ini normal & sehat — sistem tidak memaksa entry tanpa konfirmasi.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
