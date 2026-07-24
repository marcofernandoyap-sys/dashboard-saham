#!/usr/bin/env python3
"""
Audit robustness sistem yang DIADOPSI (regime + RS), BUKAN eksperimen fitur baru.

Motivasi: setelah 8 eksperimen struktural (2 adopsi: regime, RS; 6+ tolak),
sebelum pernah mempercayai Sharpe OOS 0,54 untuk live, kita audit APAKAH angka itu
kokoh atau cuma ditopang satu fold beruntung / asumsi biaya / ukuran jendela.

Tiga pemeriksaan (SEMUA pakai walk-forward OOS-blind yang sama):

  1. FOLD-BY-FOLD  (grid jujur penuh, fee=20bps, IS=504/OOS=126)
     -> reproduksi agregat 0,54 + rincian Sharpe/return/#trade tiap fold.
     Pertanyaan: adakah 1 fold yang mendominasi? Berapa fold Sharpe < 0?

  2. SENSITIVITAS BIAYA  (fee_bps in {10,15,20,30,40})
     -> parameter DIKUNCI di nilai adopsi (buy=60, atr=3.0, RR=3.0, rs_lookback=252)
     supaya efek biaya TIDAK terancukan oleh re-optimasi per level biaya.
     Pertanyaan: apakah edge bertahan saat biaya naik (slippage dunia nyata)?

  3. SENSITIVITAS JENDELA  ((is_days, oos_days) beberapa kombinasi)
     -> parameter DIKUNCI (sama seperti #2). Pertanyaan: apakah hasil artefak dari
     satu pilihan protokol walk-forward tertentu?

Ini AUDIT, bukan optimasi: tak ada yang diadopsi/ditolak di sini. Tujuannya
menakar kepercayaan pada angka yang sudah ada.

    python -m scripts.audit_robustness
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.backtest.engine import BacktestConfig
from quant.backtest.walkforward import run_walk_forward
from quant.config import SETTINGS
from quant.data.storage import Storage

# Grid terkunci = parameter adopsi (default config). Dipakai utk sweep
# sensitivitas: strategi tetap, hanya knob lingkungan/protokol yang berubah.
FIXED_GRID = {
    "buy_score_threshold": [SETTINGS.signal.buy_score_threshold],
    "atr_stop_mult": [SETTINGS.signal.atr_stop_mult],
    "min_risk_reward": [SETTINGS.signal.min_risk_reward],
}
FIXED_RS_LB = [SETTINGS.rs.lookback]


def _load():
    storage = Storage()
    tickers = storage.tickers(market="IDX")
    if not tickers:
        return None, None
    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    return ohlcv, (index_df if not index_df.empty else None)


def _fold_table(res) -> None:
    print("\n" + "=" * 78)
    print("1) FOLD-BY-FOLD OOS (grid jujur penuh, fee=20bps, IS=504/OOS=126)")
    print("=" * 78)
    print(f"{'fold':>4} {'oos_start':>11} {'oos_end':>11} "
          f"{'trade':>5} {'Sharpe':>7} {'ret%':>8} {'MDD%':>7}")
    print("-" * 78)
    sharpes = []
    for f in res.folds:
        m = f.oos_metrics
        sharpes.append(m.sharpe)
        print(f"{f.idx:>4} {f.oos_start:>11} {f.oos_end:>11} "
              f"{m.n_trades:>5} {m.sharpe:>7.2f} "
              f"{m.total_return_pct*100:>+8.1f} {m.max_drawdown_pct*100:>7.1f}")
    print("-" * 78)
    n_neg = sum(1 for s in sharpes if s < 0)
    best = max(res.folds, key=lambda f: f.oos_metrics.sharpe)
    worst = min(res.folds, key=lambda f: f.oos_metrics.sharpe)
    print(f"agregat OOS Sharpe (chained daily): {res.oos_metrics.sharpe:+.2f}  "
          f"| PF {res.oos_metrics.profit_factor:.2f}  "
          f"expR {res.oos_metrics.expectancy_r:+.2f}  "
          f"ret {res.oos_metrics.total_return_pct*100:+.1f}%  "
          f"MDD {res.oos_metrics.max_drawdown_pct*100:.1f}%")
    if sharpes:
        print(f"per-fold Sharpe: n={len(sharpes)}  "
              f"min {min(sharpes):+.2f}  median {statistics.median(sharpes):+.2f}  "
              f"max {max(sharpes):+.2f}  |  fold Sharpe<0: {n_neg}/{len(sharpes)}")
        print(f"fold terbaik: #{best.idx} ({best.oos_start}) Sharpe "
              f"{best.oos_metrics.sharpe:+.2f}  |  fold terburuk: #{worst.idx} "
              f"({worst.oos_start}) Sharpe {worst.oos_metrics.sharpe:+.2f}")
    print("Interpretasi: kalau 1 fold copot dan agregat runtuh -> rapuh. "
          "Idealnya mayoritas fold Sharpe>0 & tak ada satu fold yang mendominasi.")


def _sweep(ohlcv, index_df, capital, label, runs) -> None:
    print("\n" + "=" * 78)
    print(label)
    print("=" * 78)
    print(f"{'skenario':<22} {'trade':>5} {'Sharpe':>7} {'PF':>6} "
          f"{'expR':>7} {'ret%':>8} {'MDD%':>7}")
    print("-" * 78)
    for name, cfg, is_days, oos_days in runs:
        res = run_walk_forward(ohlcv, cfg=cfg, base_settings=SETTINGS,
                               is_days=is_days, oos_days=oos_days,
                               grid=FIXED_GRID, rs_lookback_grid=FIXED_RS_LB,
                               min_is_trades=15, index_df=index_df)
        m = res.oos_metrics
        print(f"{name:<22} {m.n_trades:>5} {m.sharpe:>7.2f} "
              f"{m.profit_factor:>6.2f} {m.expectancy_r:>+7.2f} "
              f"{m.total_return_pct*100:>+8.1f} {m.max_drawdown_pct*100:>7.1f}")
    print("-" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit robustness sistem adopsi (regime+RS)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    args = ap.parse_args()

    ohlcv, index_df = _load()
    if ohlcv is None:
        print("Belum ada data. Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1

    # 1) FOLD-BY-FOLD: grid jujur penuh (reproduksi 0,54).
    print("Menjalankan audit robustness (walk-forward OOS-blind, sistem adopsi)...")
    print("[1/3] fold-by-fold (grid penuh) — ini yang paling lama")
    core = run_walk_forward(ohlcv, cfg=BacktestConfig(initial_capital=args.capital,
                                                      fee_bps=20.0),
                            base_settings=SETTINGS, is_days=504, oos_days=126,
                            min_is_trades=15, index_df=index_df)
    _fold_table(core)

    # 2) SENSITIVITAS BIAYA (param terkunci).
    print("\n[2/3] sensitivitas biaya (param dikunci di nilai adopsi)")
    fee_runs = [(f"fee={fb:g}bps",
                 BacktestConfig(initial_capital=args.capital, fee_bps=fb),
                 504, 126)
                for fb in (10.0, 15.0, 20.0, 30.0, 40.0)]
    _sweep(ohlcv, index_df, args.capital,
           "2) SENSITIVITAS BIAYA (buy=60, atr=3.0, RR=3.0, rs_lb=252 — DIKUNCI)",
           fee_runs)

    # 3) SENSITIVITAS JENDELA (param terkunci, fee=20).
    print("\n[3/3] sensitivitas jendela IS/OOS (param dikunci)")
    win_runs = [(f"IS={i}/OOS={o}",
                 BacktestConfig(initial_capital=args.capital, fee_bps=20.0), i, o)
                for (i, o) in ((378, 126), (504, 126), (504, 189), (630, 126))]
    _sweep(ohlcv, index_df, args.capital,
           "3) SENSITIVITAS JENDELA (param DIKUNCI, fee=20bps)", win_runs)

    print("\n" + "=" * 78)
    print("CATATAN AUDIT: sweep #2/#3 mengunci parameter (analisis sensitivitas: "
          "ubah SATU knob, strategi tetap), jadi level Sharpe-nya bisa beda dari "
          "0,54 hasil re-optimasi per-fold di #1. Yang dinilai = KESTABILAN "
          "tanda & besaran, bukan kecocokan angka absolut.")
    print("Audit tidak mengadopsi/menolak apa pun. Gate live tetap butuh Sharpe>=1,0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
