#!/usr/bin/env python3
"""
Eksperimen STRUKTURAL #6: pindah timeframe HARIAN -> MINGGUAN.

Hipotesis: trend-following biasanya lebih "bersih" di bar mingguan (noise harian
berkurang) -> Sharpe out-of-sample bisa naik menembus gate live (>= 1.0). Ini
perubahan struktural besar (bukan sekadar tuning) tapi konsisten dgn metodologi:
uji WALK-FORWARD; adopsi HANYA kalau OOS jujur mengalahkan baseline harian.

Perubahan yang WAJIB agar apel-vs-apel:
  1. OHLCV di-resample W-FRI (open=first, high=max, low=min, close=last, vol=sum).
  2. Indikator waktu-berbasis disesuaikan konvensi weekly:
       ema_periods (9,21,50,200) DIPERTAHANKAN — jadi weekly EMA200 = tren
       ~4 tahun (setara daily EMA200 ~10 bulan; ini valid: banyak trader trend
       memakai 200-week MA untuk siklus panjang). Nama kolom `ema_21/50/200`
       di scoring.py hardcode -> WAJIB tetap sama. RS lookback 252d -> 52w;
       ATR%-window 252d -> 52w; max_hold 60d -> 12w; volume/OBV window 20d -> 4w.
       RSI 14, MACD 12/26/9, BB 20, ATR 14 dipertahankan (standar weekly).
  3. Anualisasi Sharpe/CAGR: periods_per_year=52 (BacktestConfig).
  4. Warmup walk-forward: 210 minggu (cukup EMA200 weekly ~ 4 tahun).
  5. Grid RS lookback disesuaikan: [4, 8, 13, 26, 52] minggu.

Baseline = walk-forward HARIAN default (Sharpe OOS ~0.54 saat ini).
Diadopsi HANYA kalau OOS mingguan > OOS harian secara Sharpe DAN tak merusak
PF/expectancy/return (persis aturan #1-#5).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.backtest.engine import BacktestConfig
from quant.backtest.walkforward import run_walk_forward
from quant.config import SETTINGS
from quant.data.resample import to_weekly, to_weekly_map
from quant.data.storage import Storage


def _weekly_settings(base):
    """
    Bangun Settings variant utk timeframe mingguan (frozen replace).

    Catatan: ema_periods DIPERTAHANKAN (9,21,50,200) supaya nama kolom
    `ema_21/50/200` yang di-hardcode di scoring._trend_components tetap valid.
    Konsekuensinya EMA200 weekly = ~4 tahun (siklus panjang) — konvensi valid
    utk weekly trend-follow.
    """
    ind = replace(
        base.indicators,
        # ema_periods sengaja dipertahankan (lihat docstring).
        obv_slope_window=4,      # 4 minggu (~1 bulan) daripada 20 hari (~1 bulan)
        volume_avg_window=4,
    )
    reg = replace(base.regime, ema_period=200)  # tak berubah (weekly EMA200)
    rs = replace(base.rs, lookback=52)          # 1 tahun (weeks)
    vf = replace(base.vol_filter, atr_pct_window=52)   # 1 tahun (weeks)
    cf = replace(base.corr_filter, lookback=13)        # ~3 bulan (weeks)
    return replace(base, indicators=ind, regime=reg, rs=rs,
                   vol_filter=vf, corr_filter=cf)


WEEKLY_RS_GRID = [4, 8, 13, 26, 52]


def _fmt_row(label, m) -> str:
    return (f"{label:<22} {m.n_trades:>5} {m.sharpe:>7.2f} "
            f"{m.profit_factor:>6.2f} {m.expectancy_r:>+7.2f} "
            f"{m.total_return_pct*100:>+8.1f} {m.max_drawdown_pct*100:>7.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Eksperimen #6 weekly (OOS-blind)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--fee-bps", type=float, default=20.0)
    # Default harian utk baseline
    ap.add_argument("--is-days-daily", type=int, default=504)
    ap.add_argument("--oos-days-daily", type=int, default=126)
    # Default mingguan (2 thn IS, ~6 bln OOS)
    ap.add_argument("--is-weeks", type=int, default=104)
    ap.add_argument("--oos-weeks", type=int, default=26)
    ap.add_argument("--warmup-weeks", type=int, default=210)
    ap.add_argument("--min-is-trades-daily", type=int, default=15)
    ap.add_argument("--min-is-trades-weekly", type=int, default=5)
    args = ap.parse_args()

    storage = Storage()
    tickers = storage.tickers(market="IDX")
    if not tickers:
        print("Belum ada data. Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1

    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None

    print("Menjalankan baseline HARIAN + variant MINGGUAN (walk-forward OOS-blind)...")
    print("[1/2] baseline harian")
    cfg_daily = BacktestConfig(initial_capital=args.capital,
                               fee_bps=args.fee_bps,
                               periods_per_year=252)
    base = run_walk_forward(ohlcv, cfg=cfg_daily, base_settings=SETTINGS,
                            is_days=args.is_days_daily,
                            oos_days=args.oos_days_daily,
                            min_is_trades=args.min_is_trades_daily,
                            index_df=index_df)

    print("[2/2] mingguan (resample W-FRI + settings weekly)")
    ohlcv_w = to_weekly_map(ohlcv)
    index_w = to_weekly(index_df) if index_df is not None else None
    settings_w = _weekly_settings(SETTINGS)
    cfg_weekly = BacktestConfig(initial_capital=args.capital,
                                fee_bps=args.fee_bps,
                                max_hold_days=12,       # ~3 bulan (weeks)
                                periods_per_year=52)
    wf_w = run_walk_forward(ohlcv_w, cfg=cfg_weekly, base_settings=settings_w,
                            is_days=args.is_weeks, oos_days=args.oos_weeks,
                            min_is_trades=args.min_is_trades_weekly,
                            index_df=index_w,
                            rs_lookback_grid=WEEKLY_RS_GRID,
                            warmup=args.warmup_weeks)

    bm, wm = base.oos_metrics, wf_w.oos_metrics
    print("\n" + "=" * 78)
    print("PERBANDINGAN OUT-OF-SAMPLE (Sharpe = metrik gate live; harus >= 1.0)")
    print("=" * 78)
    print(f"{'variant':<22} {'trade':>5} {'Sharpe':>7} {'PF':>6} "
          f"{'expR':>7} {'ret%':>8} {'MDD%':>7}")
    print("-" * 78)
    print(_fmt_row("baseline (daily)", bm))
    print(_fmt_row("weekly", wm))
    print("-" * 78)

    # Stabilitas param terpilih (RS lookback & entry threshold)
    print("\nStabilitas param weekly (fold IS-terpilih):")
    for key, counts in wf_w.param_stability.items():
        if not counts:
            continue
        items = ", ".join(f"{v}:{n}x" for v, n in sorted(counts.items()))
        print(f"  {key}: {items}")
    print(f"  #fold: {len(wf_w.folds)}")

    # Verdikt jujur
    better_sharpe = wm.sharpe > bm.sharpe + 1e-9
    not_worse = (wm.profit_factor >= bm.profit_factor - 1e-9
                 and wm.expectancy_r >= bm.expectancy_r - 1e-9
                 and wm.total_return_pct >= 0)
    verdict = "ADOPSI" if (better_sharpe and not_worse) else "TOLAK"
    dS = wm.sharpe - bm.sharpe
    print("-" * 78)
    print(f"VERDIKT: timeframe=weekly  Sharpe {wm.sharpe:.2f} "
          f"(Δ{dS:+.2f} vs daily) -> {verdict}")
    if verdict == "ADOPSI":
        print("  Weekly memperbaiki OOS. Adopsi ke default default_timeframe='weekly' "
              "(wajib update config + tes + README + resample pipeline harian).")
    else:
        print("  Tidak memperbaiki OOS. Konsisten metodologi jujur: tolak.")
    if wm.sharpe >= 1.0:
        print("  Sharpe weekly >= 1.0 -> gate live bisa LOLOS. Tetap wajib paper "
              "trading >=60 hari/>=30 trade sebelum live.")
    else:
        print("  Sharpe weekly < 1.0 -> gate live TETAP diblokir (benar).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
