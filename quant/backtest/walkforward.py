"""
Walk-forward analysis (anti curve-fitting).

Ide inti: parameter TIDAK boleh dioptimasi lalu diuji di periode yang sama
(itu overfitting). Sebaliknya:

  1. Bagi histori jadi beberapa "fold" berurutan.
  2. Di tiap fold: optimasi parameter pada jendela IN-SAMPLE (IS),
     lalu UJI parameter terpilih pada jendela OUT-OF-SAMPLE (OOS) berikutnya
     yang BELUM PERNAH dilihat saat optimasi.
  3. Gabungkan SEMUA hasil OOS -> itulah estimasi jujur performa "live".

Efisiensi: skor komposit di-cache sekali (precompute_scores). Parameter yang
di-grid (buy_score_threshold / atr_stop_mult / min_risk_reward) TIDAK mengubah
skor, hanya keputusan evaluate_buy + level stop/TP. Jadi ratusan kombinasi x
banyak fold tetap murah.

PRINSIP RISK-FIRST tetap berlaku: objektif optimasi memakai expectancy (edge per
unit risiko), BUKAN sekadar total return; kombinasi dengan trade terlalu sedikit
di IS ditolak (tidak signifikan).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from quant.backtest.engine import (Backtester, BacktestConfig,
                                    build_breadth, build_daily_returns,
                                    build_features, build_regime, build_rs,
                                    build_vol_rank, precompute_scores)
from quant.backtest.metrics import Metrics, compute_metrics
from quant.config import SETTINGS

# Grid parameter (hanya yang TIDAK mengubah skor komposit).
DEFAULT_GRID = {
    "buy_score_threshold": [45.0, 50.0, 55.0, 60.0, 65.0, 70.0],
    "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
    "min_risk_reward": [1.5, 2.0, 2.5, 3.0],
}

# Kandidat lookback filter kekuatan relatif (dipakai HANYA kalau rs.enabled).
# Masuk grid IN-SAMPLE supaya tiap fold memilih lookback tanpa melihat OOS
# (uji jujur; menghindari curve-fit lookback ke periode uji). RS tidak mengubah
# skor komposit -> cache skor tetap valid.
DEFAULT_RS_LOOKBACK_GRID = [21, 42, 63, 126, 252]


@dataclass
class Fold:
    idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    best_params: dict = field(default_factory=dict)
    is_metrics: Metrics | None = None
    oos_metrics: Metrics | None = None
    oos_daily_returns: list[float] = field(default_factory=list)
    oos_n_trades: int = 0


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_metrics: Metrics                 # agregat semua OOS (estimasi jujur)
    grid: dict
    n_combos: int
    param_stability: dict                # frekuensi tiap nilai param terpilih


def make_folds(dates: list[pd.Timestamp], is_days: int, oos_days: int,
               warmup: int = 210) -> list[tuple]:
    """
    Fold berurutan non-overlapping pada OOS (rolling walk-forward).
    Setiap fold: [is_start, is_end] optimasi, lalu [oos_start, oos_end] uji.
    `warmup` memastikan cukup histori sebelum fold pertama (EMA200 dsb.).
    """
    folds = []
    start = warmup
    n = len(dates)
    while start + is_days + oos_days <= n:
        is_lo, is_hi = start, start + is_days - 1
        oos_lo, oos_hi = start + is_days, start + is_days + oos_days - 1
        folds.append((dates[is_lo], dates[is_hi], dates[oos_lo], dates[oos_hi]))
        start += oos_days
    return folds


def _make_settings(base, buy_score_threshold: float, atr_stop_mult: float,
                   min_risk_reward: float):
    sig = replace(base.signal, buy_score_threshold=buy_score_threshold,
                  atr_stop_mult=atr_stop_mult, min_risk_reward=min_risk_reward)
    return replace(base, signal=sig)


def _objective(m: Metrics, min_trades: int) -> float:
    """
    Skor kualitas set parameter di IS. Risk-first:
      - butuh cukup trade (signifikansi statistik) -> kalau kurang, diskualifikasi.
      - utamakan expectancy (R per trade), yang harus > 0.
      - tie-break kecil dari profit factor.
    """
    if m.n_trades < min_trades or m.expectancy_r <= 0:
        return float("-inf")
    return m.expectancy_r + 0.01 * m.profit_factor


def _run_window(feat, pos_of_date, score_cache, settings, cfg,
                start: pd.Timestamp, end: pd.Timestamp, regime_ok=None,
                rs_ok=None, vol_rank=None, daily_returns=None, breadth=None):
    wcfg = replace(cfg, start=str(start.date()), end=str(end.date()))
    bt = Backtester(cfg=wcfg, settings=settings, feat=feat,
                    pos_of_date=pos_of_date, score_cache=score_cache,
                    regime_ok=regime_ok, rs_ok=rs_ok, vol_rank=vol_rank,
                    daily_returns=daily_returns, breadth=breadth)
    return bt.run()


def run_walk_forward(ohlcv_by_ticker: dict[str, pd.DataFrame],
                     cfg: BacktestConfig | None = None,
                     base_settings=SETTINGS,
                     is_days: int = 504, oos_days: int = 126,
                     grid: dict | None = None,
                     min_is_trades: int = 15,
                     index_df: pd.DataFrame | None = None,
                     rs_lookback_grid: list | None = None,
                     candidate: dict | None = None,
                     warmup: int = 210) -> WalkForwardResult:
    """
    `candidate` (opsional) = dimensi perbaikan struktural #4 yang diuji OOS-blind:
        {"name": str, "values": list, "apply": callable(settings, value)->settings}
    Tiap fold memilih nilai kandidat TERBAIK di in-sample, lalu diuji di OOS.
    None -> perilaku lama (tanpa dimensi kandidat), 100% backward-compatible.
    """
    cfg = cfg or BacktestConfig()
    grid = grid or DEFAULT_GRID

    # (1) Fitur + skor di-cache SATU KALI (mahalnya di sini saja).
    feat, pos_of_date = build_features(ohlcv_by_ticker, base_settings)
    score_cache = precompute_scores(feat, base_settings)
    regime_ok = build_regime(index_df, base_settings)
    vol_rank = build_vol_rank(feat, base_settings)
    daily_returns = build_daily_returns(feat)
    breadth = build_breadth(feat, base_settings)

    # Dimensi kandidat #4 (opsional). cvals=[None] -> tak berpengaruh.
    cand_name = candidate["name"] if candidate else None
    cvals = list(candidate["values"]) if candidate else [None]
    cand_apply = candidate["apply"] if candidate else (lambda s, v: s)

    # RS: kalau diaktifkan, sediakan peta RS per kandidat lookback (IS yang
    # memilih; OOS-blind). Kalau nonaktif, satu slot None -> tak memengaruhi apa pun.
    rs_enabled = base_settings.rs.enabled
    if rs_enabled:
        lbs = list(rs_lookback_grid or DEFAULT_RS_LOOKBACK_GRID)
        rs_maps = {lb: build_rs(ohlcv_by_ticker, index_df,
                                replace(base_settings,
                                        rs=replace(base_settings.rs, lookback=lb)))
                   for lb in lbs}
    else:
        lbs = [None]
        rs_maps = {None: None}

    # Kalender global (union tanggal), terurut.
    all_dates: set[pd.Timestamp] = set()
    for f in feat.values():
        all_dates.update(f.index)
    dates = sorted(all_dates)

    combos = [(b, a, r, lb, cv) for b in grid["buy_score_threshold"]
              for a in grid["atr_stop_mult"] for r in grid["min_risk_reward"]
              for lb in lbs for cv in cvals]

    fold_windows = make_folds(dates, is_days, oos_days, warmup=warmup)
    folds: list[Fold] = []
    oos_returns_pct: list[float] = []
    oos_r_mults: list[float] = []
    oos_daily_chain: list[float] = []
    stability = {k: {} for k in grid}
    if rs_enabled:
        stability["rs_lookback"] = {}
    if cand_name:
        stability[cand_name] = {}

    for i, (is_lo, is_hi, oos_lo, oos_hi) in enumerate(fold_windows):
        # (2) OPTIMASI di IS: cari kombinasi terbaik menurut _objective.
        best = None
        best_obj = float("-inf")
        best_is_m = None
        for (b, a, r, lb, cv) in combos:
            st = cand_apply(_make_settings(base_settings, b, a, r), cv)
            res = _run_window(feat, pos_of_date, score_cache, st, cfg,
                              is_lo, is_hi, regime_ok, rs_maps[lb], vol_rank,
                              daily_returns, breadth)
            obj = _objective(res.metrics, min_is_trades)
            if obj > best_obj:
                best_obj, best, best_is_m = obj, (b, a, r, lb, cv), res.metrics

        if best is None:            # tak ada kombinasi lolos syarat IS
            b, a, r = (base_settings.signal.buy_score_threshold,
                       base_settings.signal.atr_stop_mult,
                       base_settings.signal.min_risk_reward)
            lb = lbs[0]
            cv = cvals[0]
        else:
            b, a, r, lb, cv = best
            stability["buy_score_threshold"][b] = \
                stability["buy_score_threshold"].get(b, 0) + 1
            stability["atr_stop_mult"][a] = \
                stability["atr_stop_mult"].get(a, 0) + 1
            stability["min_risk_reward"][r] = \
                stability["min_risk_reward"].get(r, 0) + 1
            if rs_enabled:
                stability["rs_lookback"][lb] = \
                    stability["rs_lookback"].get(lb, 0) + 1
            if cand_name:
                stability[cand_name][cv] = \
                    stability[cand_name].get(cv, 0) + 1

        # (3) UJI di OOS dengan parameter terpilih (data belum pernah dilihat).
        st = cand_apply(_make_settings(base_settings, b, a, r), cv)
        oos_res = _run_window(feat, pos_of_date, score_cache, st, cfg,
                              oos_lo, oos_hi, regime_ok, rs_maps[lb], vol_rank,
                              daily_returns, breadth)

        oos_returns_pct.extend(t.return_pct for t in oos_res.trades)
        oos_r_mults.extend(t.r_multiple for t in oos_res.trades)
        # daily returns per hari OOS (untuk agregasi Sharpe/MDD berurutan)
        eq = oos_res.equity_curve
        prev = cfg.initial_capital
        for e in eq:
            oos_daily_chain.append(e / prev - 1.0 if prev else 0.0)
            prev = e

        folds.append(Fold(
            idx=i, is_start=str(is_lo.date()), is_end=str(is_hi.date()),
            oos_start=str(oos_lo.date()), oos_end=str(oos_hi.date()),
            best_params={"buy_score_threshold": b, "atr_stop_mult": a,
                         "min_risk_reward": r, "rs_lookback": lb,
                         **({cand_name: cv} if cand_name else {})},
            is_metrics=best_is_m, oos_metrics=oos_res.metrics,
            oos_daily_returns=[e / cfg.initial_capital for e in eq],
            oos_n_trades=oos_res.metrics.n_trades,
        ))

    # (4) AGREGASI OOS: bangun kurva ekuitas berantai dari daily returns OOS.
    equity = [1.0]
    for dr in oos_daily_chain:
        equity.append(equity[-1] * (1.0 + dr))
    equity = equity[1:] if equity else [1.0]
    agg = compute_metrics(oos_returns_pct, oos_r_mults, equity,
                          oos_daily_chain, n_days=len(oos_daily_chain),
                          periods_per_year=cfg.periods_per_year)

    return WalkForwardResult(
        folds=folds, oos_metrics=agg, grid=grid, n_combos=len(combos),
        param_stability=stability,
    )
