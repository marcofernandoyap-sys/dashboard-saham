#!/usr/bin/env python3
"""
CLI: walk-forward analysis (optimasi IS -> uji OOS) di data tersimpan.

Tujuan: cari tahu apakah strategi punya EDGE yang bertahan di data baru
(out-of-sample), BUKAN sekadar cocok di satu periode (curve-fitting).

Contoh:
    python -m scripts.walkforward --capital 100000000
    python -m scripts.walkforward --is-days 504 --oos-days 126 --fee-bps 20

Yang dilaporkan sebagai penilaian JUJUR adalah metrik OOS agregat.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.analysis.scoring import compute_features
from quant.analysis.screener import screen_liquidity_idx
from quant.backtest.engine import BacktestConfig
from quant.backtest.walkforward import run_walk_forward
from quant.config import SETTINGS
from quant.data.storage import Storage
from quant.universe import idx_tickers


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward analysis IDX")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--fee-bps", type=float, default=20.0)
    ap.add_argument("--is-days", type=int, default=504, help="hari bursa in-sample")
    ap.add_argument("--oos-days", type=int, default=126, help="hari bursa out-of-sample")
    ap.add_argument("--min-is-trades", type=int, default=15)
    ap.add_argument("--index", default=None,
                    help="batasi universe: LQ45/IDX30/IDX (default: semua di storage)")
    ap.add_argument("--csv", default=None, help="CSV universe untuk --index IDX")
    ap.add_argument("--tickers", nargs="*", help="override daftar ticker eksplisit")
    ap.add_argument("--liquid-only", action="store_true",
                    help="hanya uji nama yang lolos screen likuiditas produksi")
    args = ap.parse_args()

    storage = Storage()
    stored = set(storage.tickers(market="IDX"))   # indeks (^JKSE) BUKAN aset tradable
    if not stored:
        print("Belum ada data. Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1

    # Pilih universe: eksplisit --tickers > --index > semua di storage.
    if args.tickers:
        want = list(args.tickers)
    elif args.index:
        want = idx_tickers(args.index, csv_path=args.csv)
    else:
        want = list(stored)
    tickers = [t for t in want if t in stored]
    missing = [t for t in want if t not in stored]
    if missing:
        print(f"[info] {len(missing)} ticker diminta tapi tak ada di storage "
              f"(dilewati), mis. {missing[:5]}")
    if not tickers:
        print("Tak ada ticker universe yang tersedia di storage.")
        return 1

    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}

    if args.liquid_only:
        feats = {t: compute_features(df) for t, df in ohlcv.items()
                 if df is not None and not df.empty}
        liq = screen_liquidity_idx(feats, SETTINGS)
        keep = {s.ticker for s in liq if s.passed}
        before = len(ohlcv)
        ohlcv = {t: df for t, df in ohlcv.items() if t in keep}
        print(f"[liquid-only] {len(ohlcv)}/{before} nama lolos screen likuiditas.")
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None
    if SETTINGS.regime.enabled and index_df is None:
        print(f"[peringatan] data indeks {SETTINGS.regime.index_ticker} tak ada -> "
              "filter regime NONAKTIF.")
    cfg = BacktestConfig(initial_capital=args.capital, fee_bps=args.fee_bps)

    print("Menjalankan walk-forward (optimasi IS -> uji OOS)... ini bisa sebentar.")
    wf = run_walk_forward(ohlcv, cfg=cfg, base_settings=SETTINGS,
                          is_days=args.is_days, oos_days=args.oos_days,
                          min_is_trades=args.min_is_trades, index_df=index_df)

    print("\n" + "=" * 72)
    print("WALK-FORWARD — " + SETTINGS.disclaimer)
    print("=" * 72)
    print(f"Universe       : {len(ohlcv)} nama"
          + (f" ({args.index}" if args.index else " (semua storage")
          + (", liquid-only)" if args.liquid_only else ")"))
    print(f"Fold           : {len(wf.folds)}  "
          f"(IS {args.is_days} hari, OOS {args.oos_days} hari)")
    print(f"Grid kombinasi : {wf.n_combos} per fold "
          f"(hanya param non-skor: threshold/ATR/RR)")
    print("-" * 72)
    print(f"{'Fold':>4} {'OOS periode':<24} {'params(thr/atr/rr/rs)':<22} "
          f"{'trade':>5} {'ret%':>7} {'PF':>5}")
    for f in wf.folds:
        p = f.best_params
        m = f.oos_metrics
        lb = p.get('rs_lookback')
        lbstr = f"/{lb}" if lb is not None else ""
        pstr = (f"{p['buy_score_threshold']:.0f}/{p['atr_stop_mult']:.1f}/"
                f"{p['min_risk_reward']:.1f}{lbstr}")
        print(f"{f.idx:>4} {f.oos_start+'..'+f.oos_end:<24} {pstr:<22} "
              f"{m.n_trades:>5} {m.total_return_pct*100:>+6.1f} {m.profit_factor:>5.2f}")

    m = wf.oos_metrics
    print("-" * 72)
    print("AGREGAT OUT-OF-SAMPLE (penilaian jujur, seolah dijalankan live):")
    print(f"  Jumlah trade    : {m.n_trades}")
    print(f"  Win rate        : {m.win_rate*100:.1f}%")
    print(f"  Avg return/trade: {m.avg_return_pct:+.2f}%  (median {m.median_return_pct:+.2f}%)")
    print(f"  Profit factor   : {m.profit_factor:.2f}")
    print(f"  Expectancy      : {m.expectancy_r:+.2f} R")
    print(f"  Max drawdown    : {m.max_drawdown_pct*100:.1f}%")
    print(f"  Sharpe (annual) : {m.sharpe:.2f}")
    print(f"  Total return    : {m.total_return_pct*100:+.1f}%")

    print("-" * 72)
    print("Stabilitas parameter terpilih (sering berubah = kurang robust):")
    for k, counts in wf.param_stability.items():
        if counts:
            items = ", ".join(f"{v}:{n}x" for v, n in sorted(counts.items()))
            print(f"  {k:<22}: {items}")

    print("-" * 72)
    profitable = (m.profit_factor >= 1.0 and m.expectancy_r > 0
                  and m.total_return_pct > 0)
    if profitable:
        print("KESIMPULAN: ada indikasi EDGE bertahan out-of-sample. "
              "Tetap wajib paper trading >=60 hari/>=30 trade sebelum live.")
    else:
        print("KESIMPULAN: TIDAK ada edge OOS yang meyakinkan. JANGAN paksakan "
              "ke live. Perlu perubahan STRUKTURAL (fitur/logika), bukan tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
