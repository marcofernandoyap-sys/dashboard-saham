"""
Persistensi hasil backtest + gerbang kesiapan live.

Aturan spec: mode live trading TIDAK boleh aktif kalau backtest belum dijalankan.
Modul ini menyimpan hasil backtest ke disk dan menyediakan pemeriksaan kesiapan
yang WAJIB dipanggil execution engine (fase berikutnya) sebelum mengizinkan live.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from quant.backtest.engine import BacktestResult
from quant.config import DATA_DIR, SETTINGS

BACKTEST_DIR = DATA_DIR / "backtests"


def save_result(result: BacktestResult, label: str = "") -> Path:
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"backtest_{ts}{('_' + label) if label else ''}.json"
    path = BACKTEST_DIR / name
    payload = {
        "created_utc": ts,
        "label": label,
        "config": asdict(result.config),
        "metrics": result.metrics.as_dict(),
        "n_trades": result.metrics.n_trades,
        "period": {"start": result.dates[0] if result.dates else None,
                   "end": result.dates[-1] if result.dates else None,
                   "n_days": len(result.dates)},
        "circuit_breaker_events": result.circuit_breaker_events,
        "trades": [asdict(t) for t in result.trades],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_latest() -> dict | None:
    if not BACKTEST_DIR.exists():
        return None
    files = sorted(BACKTEST_DIR.glob("backtest_*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


def live_readiness(settings=SETTINGS) -> dict:
    """
    Evaluasi apakah live trading BOLEH diaktifkan. Mengembalikan dict dengan
    'allowed' (bool) + daftar 'blockers'. Ini gerbang backtest saja; syarat
    paper-trading (>=60 hari & >=30 trade) diverifikasi di fase eksekusi.
    """
    r = settings.risk
    blockers: list[str] = []
    latest = load_latest()

    if r.require_backtest_before_live and latest is None:
        blockers.append("belum ada backtest yang tersimpan (wajib sebelum live)")
    elif latest is not None:
        n = latest.get("n_trades", 0)
        if n < r.min_recorded_trades:
            blockers.append(
                f"backtest baru mencatat {n} trade (<{r.min_recorded_trades} "
                "minimum untuk evaluasi statistik)"
            )
        # Gerbang PROFITABILITAS: strategi rugi TIDAK boleh naik ke live.
        # (Jangan pernah bypass ini demi mengejar target return.)
        m = latest.get("metrics", {})
        pf = m.get("profit_factor", 0.0)
        exp_r = m.get("expectancy_r", 0.0)
        total_ret = m.get("total_return_pct", 0.0)
        sharpe = m.get("sharpe", 0.0)
        if pf < 1.0:
            blockers.append(f"profit factor {pf:.2f} < 1.0 (strategi tidak profitable)")
        if exp_r <= 0:
            blockers.append(f"expectancy {exp_r:+.2f} R <= 0 (edge negatif/nol)")
        if total_ret <= 0:
            blockers.append(f"total return {total_ret*100:+.1f}% <= 0")
        if sharpe < 1.0:
            blockers.append(f"Sharpe {sharpe:.2f} < 1.0 (risk-adjusted return lemah)")

    return {"allowed": len(blockers) == 0, "blockers": blockers,
            "latest_backtest": latest["created_utc"] if latest else None}
