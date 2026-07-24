"""
Metrik evaluasi hasil backtest.

Semua fungsi murni (list/array in, angka out) supaya gampang di-uji.
Metrik wajib (sesuai spec): win rate, average return per trade, max drawdown,
Sharpe ratio. Ditambah profit factor & expectancy (R) untuk evaluasi lebih utuh.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

TRADING_DAYS_PER_YEAR = 252


@dataclass
class Metrics:
    n_trades: int
    win_rate: float                 # 0..1
    avg_return_pct: float           # rata-rata return per trade (%)
    median_return_pct: float
    profit_factor: float            # gross_profit / gross_loss
    expectancy_r: float             # ekspektasi dalam kelipatan R (risiko awal)
    max_drawdown_pct: float         # negatif (mis. -0.12 = -12%)
    sharpe: float                   # disetahunkan
    total_return_pct: float         # return ekuitas keseluruhan
    cagr_pct: float

    def as_dict(self) -> dict:
        return asdict(self)


def win_rate(returns_pct: list[float]) -> float:
    if not returns_pct:
        return 0.0
    wins = sum(1 for r in returns_pct if r > 0)
    return wins / len(returns_pct)


def profit_factor(returns_pct: list[float]) -> float:
    gross_profit = sum(r for r in returns_pct if r > 0)
    gross_loss = -sum(r for r in returns_pct if r < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def max_drawdown(equity_curve: list[float]) -> float:
    """Max drawdown dari kurva ekuitas. Return negatif (0 kalau tak ada DD)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = v / peak - 1.0
            mdd = min(mdd, dd)
    return mdd


def sharpe_ratio(daily_returns: list[float],
                 risk_free_daily: float = 0.0,
                 periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Sharpe disetahunkan dari deret return harian ekuitas."""
    if len(daily_returns) < 2:
        return 0.0
    excess = [r - risk_free_daily for r in daily_returns]
    mean = sum(excess) / len(excess)
    var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def cagr(equity_curve: list[float], n_days: int,
         periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    if not equity_curve or equity_curve[0] <= 0 or n_days <= 0:
        return 0.0
    total = equity_curve[-1] / equity_curve[0]
    years = n_days / periods_per_year
    if years <= 0:
        return 0.0
    return total ** (1 / years) - 1.0


def compute_metrics(trade_returns_pct: list[float],
                    trade_r_multiples: list[float],
                    equity_curve: list[float],
                    daily_returns: list[float],
                    n_days: int,
                    periods_per_year: int = TRADING_DAYS_PER_YEAR) -> Metrics:
    srt = sorted(trade_returns_pct)
    median = srt[len(srt) // 2] if srt else 0.0
    exp_r = (sum(trade_r_multiples) / len(trade_r_multiples)
             if trade_r_multiples else 0.0)
    total_ret = (equity_curve[-1] / equity_curve[0] - 1.0
                 if equity_curve and equity_curve[0] > 0 else 0.0)
    return Metrics(
        n_trades=len(trade_returns_pct),
        win_rate=win_rate(trade_returns_pct),
        avg_return_pct=(sum(trade_returns_pct) / len(trade_returns_pct)
                        if trade_returns_pct else 0.0),
        median_return_pct=median,
        profit_factor=profit_factor(trade_returns_pct),
        expectancy_r=exp_r,
        max_drawdown_pct=max_drawdown(equity_curve),
        sharpe=sharpe_ratio(daily_returns, periods_per_year=periods_per_year),
        total_return_pct=total_ret,
        cagr_pct=cagr(equity_curve, n_days, periods_per_year=periods_per_year),
    )
