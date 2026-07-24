#!/usr/bin/env python3
"""
Eksperimen perbaikan STRUKTURAL #4 (naikkan Sharpe OOS agar gate live lolos).

Metodologi JUJUR (sama seperti RS [diterima] & trailing [ditolak]):
  - Tiap kandidat diuji WALK-FORWARD: nilainya dipilih di IN-SAMPLE tiap fold,
    lalu diuji di OUT-OF-SAMPLE yang belum pernah dilihat (OOS-blind).
  - Baseline = tanpa kandidat. Kandidat DIADOPSI hanya jika OOS-nya lebih baik
    dari baseline (Sharpe utama; cek juga PF/expectancy/return/MDD/#trade).
  - Kalau memperburuk OOS -> DITOLAK (jangan curve-fit ke satu periode).

Tiga kandidat yang diuji:
  #4A vol_filter        : tolak entry saat ATR% di persentil tinggi (chop/euforia)
  #4B max_position_weight: cap bobot notional 1 posisi thd ekuitas (konsentrasi)
  #4C scaleout          : ambil sebagian di +trigger_r, stop sisanya ke breakeven

Contoh:
    python -m scripts.experiment_structural
    python -m scripts.experiment_structural --is-days 504 --oos-days 126
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
from quant.data.storage import Storage


# --- Fungsi apply kandidat (settings, value) -> settings baru (frozen replace) ---
def _apply_vol(s, pct):
    return replace(s, vol_filter=replace(s.vol_filter, enabled=True,
                                         max_percentile=pct))


def _apply_weight(s, w):
    return replace(s, risk=replace(s.risk, max_position_weight=w))


def _apply_scaleout(s, trig):
    return replace(s, scaleout=replace(s.scaleout, enabled=True, trigger_r=trig))


CANDIDATES = [
    {"name": "vol_filter.max_percentile", "values": [0.6, 0.7, 0.8, 0.9],
     "apply": _apply_vol},
    {"name": "risk.max_position_weight", "values": [0.2, 0.3, 0.5],
     "apply": _apply_weight},
    {"name": "scaleout.trigger_r", "values": [1.0, 1.5, 2.0],
     "apply": _apply_scaleout},
]


def _fmt_row(label: str, m) -> str:
    return (f"{label:<26} {m.n_trades:>5} {m.sharpe:>7.2f} "
            f"{m.profit_factor:>6.2f} {m.expectancy_r:>+7.2f} "
            f"{m.total_return_pct*100:>+8.1f} {m.max_drawdown_pct*100:>7.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Eksperimen struktural #4 (OOS-blind)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--fee-bps", type=float, default=20.0)
    ap.add_argument("--is-days", type=int, default=504)
    ap.add_argument("--oos-days", type=int, default=126)
    ap.add_argument("--min-is-trades", type=int, default=15)
    args = ap.parse_args()

    storage = Storage()
    tickers = storage.tickers(market="IDX")
    if not tickers:
        print("Belum ada data. Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1

    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None
    cfg = BacktestConfig(initial_capital=args.capital, fee_bps=args.fee_bps)

    def run(candidate):
        return run_walk_forward(
            ohlcv, cfg=cfg, base_settings=SETTINGS,
            is_days=args.is_days, oos_days=args.oos_days,
            min_is_trades=args.min_is_trades, index_df=index_df,
            candidate=candidate)

    print("Menjalankan baseline + 3 kandidat (walk-forward OOS-blind)... sabar ya.")
    print("[1/4] baseline")
    base = run(None)

    results = []  # (label, WalkForwardResult)
    for i, cand in enumerate(CANDIDATES, start=2):
        print(f"[{i}/4] {cand['name']}  values={cand['values']}")
        results.append((cand["name"], run(cand)))

    bm = base.oos_metrics
    print("\n" + "=" * 78)
    print("PERBANDINGAN OUT-OF-SAMPLE (penilaian jujur; Sharpe = metrik gate live)")
    print("=" * 78)
    hdr = (f"{'kandidat':<26} {'trade':>5} {'Sharpe':>7} {'PF':>6} "
           f"{'expR':>7} {'ret%':>8} {'MDD%':>7}")
    print(hdr)
    print("-" * 78)
    print(_fmt_row("baseline (tanpa)", bm))
    for label, wf in results:
        print(_fmt_row(label, wf.oos_metrics))
    print("-" * 78)

    # Adopsi jujur: Sharpe OOS naik DAN tidak merusak PF/expectancy/return.
    print("VERDIKT (adopsi hanya bila memperbaiki OOS, terutama Sharpe):")
    any_adopt = False
    for label, wf in results:
        m = wf.oos_metrics
        better_sharpe = m.sharpe > bm.sharpe + 1e-9
        not_worse = (m.profit_factor >= bm.profit_factor - 1e-9
                     and m.expectancy_r >= bm.expectancy_r - 1e-9
                     and m.total_return_pct >= 0)
        verdict = "ADOPSI" if (better_sharpe and not_worse) else "TOLAK"
        if verdict == "ADOPSI":
            any_adopt = True
        dS = m.sharpe - bm.sharpe
        print(f"  {label:<26} Sharpe {m.sharpe:>5.2f} (Δ{dS:>+5.2f}) -> {verdict}")
        # cetak stabilitas nilai kandidat terpilih (robust jika konsisten)
        counts = wf.param_stability.get(label, {})
        if counts:
            items = ", ".join(f"{v}:{n}x" for v, n in sorted(counts.items()))
            print(f"      nilai terpilih per fold: {items}")

    print("-" * 78)
    gate = 1.0
    best_label, best_sharpe = "baseline", bm.sharpe
    for label, wf in results:
        if wf.oos_metrics.sharpe > best_sharpe:
            best_label, best_sharpe = label, wf.oos_metrics.sharpe
    print(f"Sharpe terbaik: {best_label} = {best_sharpe:.2f} "
          f"(gate live butuh >= {gate:.1f}).")
    if best_sharpe >= gate:
        print("=> Ada kandidat yang MELEWATI ambang gate. Tetap wajib paper "
              "trading >=60 hari/>=30 trade sebelum live.")
    else:
        print("=> Belum ada yang menembus gate Sharpe>=1.0. Jangan paksakan live; "
              "adopsi yang memperbaiki lalu lanjut riset struktural.")
    if not any_adopt:
        print("   (Tidak ada kandidat yang lolos syarat adopsi — jujur: tolak semua.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
