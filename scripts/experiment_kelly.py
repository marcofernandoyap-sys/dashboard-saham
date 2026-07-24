#!/usr/bin/env python3
"""
Eksperimen STRUKTURAL #7: sizing Kelly-fraksi ADAPTIF.

Ide (belum dicoba di #1-#6): alih-alih risiko flat 1% modal/trade, skala risiko
mengikuti EDGE terkini strategi (expektansi R dari trade yang sudah ditutup).
Perbesar saat sistem "panas", perkecil saat rugi beruntun -> harapan: kurva
ekuitas lebih halus -> Sharpe naik menembus gate live (>= 1.0).

Kenapa ini beda dari sizing biasa & dari #4C (scale-out, ditolak): #4C memotong
PEMENANG (rusak compounding). Kelly-fraksi TIDAK memotong posisi; ia hanya
mengubah UKURAN entry baru berdasarkan performa historis kausal — pemenang tetap
dibiarkan penuh sampai TP/stop.

Metodologi JUJUR (sama seperti #1-#6):
  - kelly_fraction dipilih PER FOLD dari in-sample; diuji di OOS yang belum
    pernah dilihat -> tak bisa curve-fit ke data uji.
  - Baseline = flat sizing. Adopsi HANYA kalau OOS lebih baik (Sharpe utama;
    cek juga PF/expectancy/return/MDD/#trade).

GERBANG RISIKO tetap: scale di-clamp agar risiko efektif tak pernah > hard cap
(2%). Kelly hanya boleh memperbesar sampai batas keras yang sama.

Contoh:
    python -m scripts.experiment_kelly
    python -m scripts.experiment_kelly --is-days 504 --oos-days 126
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


def _apply_kelly(s, kf):
    return replace(s, kelly=replace(s.kelly, enabled=True, kelly_fraction=kf))


CANDIDATE = {
    "name": "kelly.kelly_fraction",
    "values": [0.5, 1.0, 2.0, 3.0],
    "apply": _apply_kelly,
}


def _fmt_row(label, m) -> str:
    return (f"{label:<28} {m.n_trades:>5} {m.sharpe:>7.2f} "
            f"{m.profit_factor:>6.2f} {m.expectancy_r:>+7.2f} "
            f"{m.total_return_pct*100:>+8.1f} {m.max_drawdown_pct*100:>7.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Eksperimen struktural #7 (OOS-blind)")
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

    print("Menjalankan baseline + kandidat #7 (walk-forward OOS-blind)... sabar ya.")
    print("[1/2] baseline (flat sizing)")
    base = run_walk_forward(ohlcv, cfg=cfg, base_settings=SETTINGS,
                            is_days=args.is_days, oos_days=args.oos_days,
                            min_is_trades=args.min_is_trades, index_df=index_df)
    print(f"[2/2] {CANDIDATE['name']}  values={CANDIDATE['values']}")
    cand = run_walk_forward(ohlcv, cfg=cfg, base_settings=SETTINGS,
                            is_days=args.is_days, oos_days=args.oos_days,
                            min_is_trades=args.min_is_trades, index_df=index_df,
                            candidate=CANDIDATE)

    bm, cm = base.oos_metrics, cand.oos_metrics
    print("\n" + "=" * 82)
    print("PERBANDINGAN OUT-OF-SAMPLE (Sharpe = metrik gate live; harus >= 1.0)")
    print("=" * 82)
    print(f"{'kandidat':<28} {'trade':>5} {'Sharpe':>7} {'PF':>6} "
          f"{'expR':>7} {'ret%':>8} {'MDD%':>7}")
    print("-" * 82)
    print(_fmt_row("baseline (flat)", bm))
    print(_fmt_row(CANDIDATE["name"], cm))
    print("-" * 82)

    counts = cand.param_stability.get(CANDIDATE["name"], {})
    if counts:
        items = ", ".join(f"{v}:{n}x" for v, n in sorted(counts.items()))
        print(f"nilai terpilih per fold: {items}")

    better_sharpe = cm.sharpe > bm.sharpe + 1e-9
    not_worse = (cm.profit_factor >= bm.profit_factor - 1e-9
                 and cm.expectancy_r >= bm.expectancy_r - 1e-9
                 and cm.total_return_pct >= 0)
    verdict = "ADOPSI" if (better_sharpe and not_worse) else "TOLAK"
    dS = cm.sharpe - bm.sharpe
    print("-" * 82)
    print(f"VERDIKT: {CANDIDATE['name']}  Sharpe {cm.sharpe:.2f} "
          f"(Δ{dS:+.2f}) -> {verdict}")
    if verdict == "ADOPSI":
        print("  Kandidat #7 memperbaiki OOS. Adopsi ke default (wajib update "
              "config + tes + README).")
    else:
        print("  Tidak memperbaiki OOS. Konsisten metodologi jujur: tolak.")
    if cm.sharpe >= 1.0:
        print("  Sharpe >= 1.0 -> gate live bisa LOLOS. Tetap wajib paper "
              "trading >=60 hari/>=30 trade sebelum live.")
    else:
        print("  Sharpe < 1.0 -> gate live TETAP diblokir (benar).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
