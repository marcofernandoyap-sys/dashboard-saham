#!/usr/bin/env python3
"""
Eksperimen JUJUR: per-fold re-optimasi (adaptif) vs parameter DIKUNCI (fixed).

LATAR:
  Audit sebelumnya menemukan hal aneh: walk-forward dengan grid PENUH yang
  di-re-optimasi tiap fold -> OOS Sharpe ~0,54; tapi parameter DIKUNCI di nilai
  default (buy=60/atr=3,0/RR=3,0/rs_lb=252) -> ~0,72. Hipotesis: grid search
  per-fold justru menambah NOISE overfit (memilih param yang menang di IS tapi
  tak generalisasi ke OOS), sehingga MENGUNCI param lebih baik.

BAHAYA (kenapa 1 perbandingan TIDAK cukup):
  Angka 0,72 itu untuk SATU set param (default config). Kalau default itu sendiri
  pernah "diintip" dari seluruh histori, maka 0,72 adalah SELECTION BIAS
  (in-sample), bukan bukti "mengunci lebih baik". Membandingkan 0,54 vs 0,72
  begitu saja bisa menipu diri sendiri.

DESAIN JUJUR (uji distribusi, bukan satu titik):
  1. ADAPTIF  : run_walk_forward grid penuh, re-optimasi tiap fold (OOS-blind).
                -> ini baseline jujur (~0,54).
  2. FIXED-ALL: jalankan SETIAP kombinasi grid sebagai param TERKUNCI di semua
                fold (tanpa re-optimasi), kumpulkan sebaran OOS Sharpe.
  3. Tempatkan default di dalam sebaran itu:
       - Kalau default dekat PUNCAK sebaran -> default cherry-picked ->
         0,72 = selection bias, BUKAN efek "mengunci lebih baik". TOLAK.
       - Kalau MAYORITAS kombinasi terkunci mengalahkan adaptif (0,54), dan
         default cuma anggota BIASA dari sebaran -> mengunci memang membantu,
         terlepas dari param mana yang dipilih -> efek NYATA. Layak ditindak.

  Ini menjawab: apakah "buang re-optimasi per-fold" itu edge sungguhan atau
  cuma satu param beruntung yang kebetulan jadi default.

  python -m scripts.experiment_fixed_vs_adaptive                 # LQ45 (default)
  python -m scripts.experiment_fixed_vs_adaptive --index IDX --liquid-only
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import dataclass, replace

from quant.analysis.scoring import compute_features
from quant.analysis.screener import screen_liquidity_idx
from quant.backtest.engine import (BacktestConfig, build_breadth,
                                    build_daily_returns, build_features,
                                    build_regime, build_rs, build_vol_rank,
                                    precompute_scores)
from quant.backtest.metrics import Metrics, compute_metrics
from quant.backtest.walkforward import (DEFAULT_GRID, DEFAULT_RS_LOOKBACK_GRID,
                                        _make_settings, _run_window, make_folds,
                                        run_walk_forward)
from quant.config import SETTINGS
from quant.data.storage import Storage
from quant.universe import LQ45, idx_tickers

# Param default config = kandidat "fixed" yang menghasilkan 0,72 di audit.
DEFAULT_COMBO = {
    "buy_score_threshold": SETTINGS.signal.buy_score_threshold,
    "atr_stop_mult": SETTINGS.signal.atr_stop_mult,
    "min_risk_reward": SETTINGS.signal.min_risk_reward,
    "rs_lookback": SETTINGS.rs.lookback if SETTINGS.rs.enabled else None,
}


def _select_universe(storage: Storage, args) -> list[str]:
    stored = set(storage.tickers(market="IDX"))
    if args.tickers:
        want = list(args.tickers)
    elif args.index and args.index.upper() not in {"LQ45"}:
        want = idx_tickers(args.index, csv_path=args.csv)
    else:
        want = [f"{c}.JK" for c in LQ45]
    return [t for t in want if t in stored]


@dataclass
class _Cache:
    """Fitur/skor/RS dibangun SEKALI, dipakai ulang untuk semua kombinasi fixed."""
    feat: dict
    pos_of_date: dict
    score_cache: object
    regime_ok: object
    vol_rank: object
    daily_returns: object
    breadth: object
    rs_maps: dict
    dates: list


def _build_cache(ohlcv, index_df) -> _Cache:
    feat, pos_of_date = build_features(ohlcv, SETTINGS)
    score_cache = precompute_scores(feat, SETTINGS)
    regime_ok = build_regime(index_df, SETTINGS)
    vol_rank = build_vol_rank(feat, SETTINGS)
    daily_returns = build_daily_returns(feat)
    breadth = build_breadth(feat, SETTINGS)
    if SETTINGS.rs.enabled:
        rs_maps = {lb: build_rs(ohlcv, index_df,
                                replace(SETTINGS,
                                        rs=replace(SETTINGS.rs, lookback=lb)))
                   for lb in DEFAULT_RS_LOOKBACK_GRID}
    else:
        rs_maps = {None: None}
    all_dates: set = set()
    for f in feat.values():
        all_dates.update(f.index)
    return _Cache(feat, pos_of_date, score_cache, regime_ok, vol_rank,
                  daily_returns, breadth, rs_maps, sorted(all_dates))


def _fixed_oos(cache: _Cache, combo: dict, cfg, is_days, oos_days,
               warmup: int = 210) -> Metrics:
    """
    OOS agregat kalau param `combo` DIKUNCI dari hari pertama (tanpa IS re-opt,
    tanpa fallback). Jendela OOS & rantai daily-return IDENTIK dengan
    run_walk_forward, jadi SATU-SATUNYA beda vs adaptif = tak ada re-optimasi.
    """
    st = _make_settings(SETTINGS, combo["buy_score_threshold"],
                        combo["atr_stop_mult"], combo["min_risk_reward"])
    rs_ok = cache.rs_maps[combo["rs_lookback"]]
    windows = make_folds(cache.dates, is_days, oos_days, warmup=warmup)
    oos_returns_pct: list = []
    oos_r_mults: list = []
    oos_daily_chain: list = []
    for (_is_lo, _is_hi, oos_lo, oos_hi) in windows:
        res = _run_window(cache.feat, cache.pos_of_date, cache.score_cache, st,
                          cfg, oos_lo, oos_hi, cache.regime_ok, rs_ok,
                          cache.vol_rank, cache.daily_returns, cache.breadth)
        oos_returns_pct.extend(t.return_pct for t in res.trades)
        oos_r_mults.extend(t.r_multiple for t in res.trades)
        prev = cfg.initial_capital
        for e in res.equity_curve:
            oos_daily_chain.append(e / prev - 1.0 if prev else 0.0)
            prev = e
    equity = [1.0]
    for dr in oos_daily_chain:
        equity.append(equity[-1] * (1.0 + dr))
    equity = equity[1:] if equity else [1.0]
    return compute_metrics(oos_returns_pct, oos_r_mults, equity, oos_daily_chain,
                           n_days=len(oos_daily_chain),
                           periods_per_year=cfg.periods_per_year)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Uji jujur: adaptif (re-optimasi per-fold) vs param dikunci")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--fee-bps", type=float, default=20.0)
    ap.add_argument("--is-days", type=int, default=504)
    ap.add_argument("--oos-days", type=int, default=126)
    ap.add_argument("--min-is-trades", type=int, default=15)
    ap.add_argument("--index", default="LQ45",
                    help="LQ45 (default) / IDX30 / IDX (butuh --csv)")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--liquid-only", action="store_true",
                    help="saring universe lewat screen likuiditas produksi")
    args = ap.parse_args()

    storage = Storage()
    tickers = _select_universe(storage, args)
    if not tickers:
        print("Tak ada ticker universe di storage. "
              "Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1
    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}

    if args.liquid_only:
        feats = {t: compute_features(df) for t, df in ohlcv.items()
                 if df is not None and not df.empty}
        keep = {s.ticker for s in screen_liquidity_idx(feats, SETTINGS) if s.passed}
        before = len(ohlcv)
        ohlcv = {t: df for t, df in ohlcv.items() if t in keep}
        print(f"[liquid-only] {len(ohlcv)}/{before} nama lolos screen.")

    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None
    cfg = BacktestConfig(initial_capital=args.capital, fee_bps=args.fee_bps)

    # Semua kombinasi grid (untuk sebaran fixed). rs_lb ikut kalau RS aktif.
    rs_grid = (DEFAULT_RS_LOOKBACK_GRID if SETTINGS.rs.enabled else [None])
    combos = [
        {"buy_score_threshold": b, "atr_stop_mult": a,
         "min_risk_reward": r, "rs_lookback": lb}
        for b in DEFAULT_GRID["buy_score_threshold"]
        for a in DEFAULT_GRID["atr_stop_mult"]
        for r in DEFAULT_GRID["min_risk_reward"]
        for lb in rs_grid
    ]

    print("=" * 78)
    print("EKSPERIMEN JUJUR: ADAPTIF (re-optimasi/fold) vs PARAM DIKUNCI")
    print("=" * 78)
    print(f"Universe : {len(ohlcv)} nama ({args.index}"
          + (", liquid-only)" if args.liquid_only else ")"))
    print(f"Protokol : IS={args.is_days} / OOS={args.oos_days}, fee={args.fee_bps:g}bps")
    print(f"Grid     : {len(combos)} kombinasi terkunci akan diuji satu-satu")

    # (1) ADAPTIF: baseline jujur (~0,54).
    print("\n[1/2] menjalankan ADAPTIF (grid penuh, re-optimasi tiap fold)...")
    adaptive = run_walk_forward(ohlcv, cfg=cfg, base_settings=SETTINGS,
                                is_days=args.is_days, oos_days=args.oos_days,
                                min_is_trades=args.min_is_trades,
                                index_df=index_df)
    a = adaptive.oos_metrics
    print(f"      ADAPTIF OOS: Sharpe {a.sharpe:+.3f}  PF {a.profit_factor:.2f}  "
          f"expR {a.expectancy_r:+.2f}  ret {a.total_return_pct*100:+.1f}%  "
          f"trade {a.n_trades}")

    # (2) FIXED-ALL: setiap kombinasi dikunci di semua fold.
    print(f"\n[2/2] membangun cache fitur/skor/RS (sekali), lalu "
          f"{len(combos)} kombinasi TERKUNCI...")
    cache = _build_cache(ohlcv, index_df)
    rows = []
    default_row = None
    for i, combo in enumerate(combos, 1):
        m = _fixed_oos(cache, combo, cfg, args.is_days, args.oos_days)
        row = {"combo": combo, "sharpe": m.sharpe, "pf": m.profit_factor,
               "expr": m.expectancy_r, "ret": m.total_return_pct,
               "trades": m.n_trades}
        rows.append(row)
        if combo == DEFAULT_COMBO:
            default_row = row
        if i % 40 == 0 or i == len(combos):
            print(f"      ...{i}/{len(combos)} selesai")

    sharpes = sorted((r["sharpe"] for r in rows), reverse=True)
    n = len(sharpes)
    beat_adaptive = sum(1 for s in sharpes if s > a.sharpe)
    best = max(rows, key=lambda r: r["sharpe"])
    worst = min(rows, key=lambda r: r["sharpe"])
    median_s = statistics.median(sharpes)

    print("\n" + "=" * 78)
    print("HASIL — sebaran OOS Sharpe untuk param TERKUNCI (tanpa re-optimasi)")
    print("=" * 78)
    print(f"Baseline ADAPTIF (re-optimasi/fold) : Sharpe {a.sharpe:+.3f}")
    print("-" * 78)
    print(f"Fixed n={n} kombinasi:")
    print(f"  Sharpe  min {min(sharpes):+.3f}  median {median_s:+.3f}  "
          f"max {max(sharpes):+.3f}")
    bc = best["combo"]
    print(f"  terbaik : Sharpe {best['sharpe']:+.3f}  "
          f"(buy={bc['buy_score_threshold']:.0f}/atr={bc['atr_stop_mult']:.1f}/"
          f"RR={bc['min_risk_reward']:.1f}/rs={bc['rs_lookback']})")
    wc = worst["combo"]
    print(f"  terburuk: Sharpe {worst['sharpe']:+.3f}  "
          f"(buy={wc['buy_score_threshold']:.0f}/atr={wc['atr_stop_mult']:.1f}/"
          f"RR={wc['min_risk_reward']:.1f}/rs={wc['rs_lookback']})")
    print(f"  kombinasi terkunci yang MENGALAHKAN adaptif: "
          f"{beat_adaptive}/{n} ({beat_adaptive/n*100:.0f}%)")

    print("-" * 78)
    if default_row is not None:
        ds = default_row["sharpe"]
        # peringkat default (1 = terbaik) & persentil.
        rank = sum(1 for s in sharpes if s > ds) + 1
        pct = (n - rank) / (n - 1) * 100 if n > 1 else 100.0
        print(f"DEFAULT config (buy=60/atr=3,0/RR=3,0/rs=252) terkunci:")
        print(f"  Sharpe {ds:+.3f}  |  peringkat {rank}/{n} "
              f"(persentil {pct:.0f} dari sebaran fixed)")
        print(f"  PF {default_row['pf']:.2f}  expR {default_row['expr']:+.2f}  "
              f"ret {default_row['ret']*100:+.1f}%  trade {default_row['trades']}")
    else:
        print("[catatan] DEFAULT combo tidak ada di grid — tak bisa cek selection bias.")

    print("=" * 78)
    print("BACA HASIL (jujur):")
    print("  - Default di PUNCAK sebaran (persentil >~85)  -> kemungkinan besar")
    print("    cherry-picked; 0,72 = SELECTION BIAS, bukan efek 'mengunci lebih")
    print("    baik'. JANGAN adopsi.")
    print("  - MAYORITAS fixed mengalahkan adaptif & default cuma anggota biasa")
    print("    -> mengunci param memang membantu (re-optimasi/fold = noise overfit).")
    print("    Efek NYATA. Layak dipertimbangkan (tetap gate live Sharpe>=1,0).")
    print("  - Fixed tersebar & tak konsisten > adaptif -> tak ada sinyal jelas.")
    print("Gate live TIDAK berubah. Ini uji pemahaman, bukan optimasi produksi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
