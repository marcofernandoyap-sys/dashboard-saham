"""
Backtesting engine portfolio (watchlist utama IDX).

Prinsip & asumsi (didokumentasikan supaya hasil bisa dipercaya/di-audit):

  NO LOOKAHEAD
    - Semua indikator kausal (rolling/ewm) -> hanya pakai data <= hari itu.
    - Sinyal dihitung dari close hari t; ENTRY di close hari t (harga yang sudah
      diketahui saat sinyal muncul). Pengecekan EXIT baru mulai hari t+1.

  EXIT
    - Stop loss (low <= stop) atau take profit (high >= tp), berbasis ATR/RR
      dari signal engine. Jika keduanya kena di hari yang sama -> diasumsikan
      STOP dulu (konservatif). Posisi tersisa di akhir data ditutup di close.

  BIAYA
    - fee_bps dikenakan tiap sisi (beli & jual). Default konservatif untuk IDX
      (~ termasuk levy/tax). Backtest tanpa biaya = menyesatkan.

  RISK MANAGEMENT (mengikuti config)
    - Position sizing dari ekuitas berjalan & risiko per trade.
    - Lot IDX: saham dibulatkan ke kelipatan 100.
    - Max posisi terbuka (watchlist utama).
    - Circuit breaker: DD harian > 3% blokir entry hari itu; DD mingguan > 7%
      blokir entry sisa minggu itu (mensimulasikan "stop + review manual").

  CAKUPAN
    - Hanya watchlist utama. Tier spekulatif TIDAK di-backtest di sini (aturan
      risikonya beda & datanya kurang andal). Screening likuiditas dianggap
      terpenuhi karena universe = LQ45/IDX30.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant.analysis.scoring import compute_features, score_ticker
from quant.analysis.signals import evaluate_buy
from quant.backtest.metrics import Metrics, compute_metrics
from quant.config import SETTINGS


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000_000.0
    fee_bps: float = 20.0            # 0.20% per sisi
    lot_size: int = 100              # 1 lot IDX = 100 lembar
    max_hold_days: int = 60          # time stop (hindari posisi menggantung)
    start: str | None = None
    end: str | None = None


@dataclass
class Trade:
    ticker: str
    entry_date: str
    entry_price: float
    shares: int
    stop: float
    take_profit: float
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""            # SL / TP / TIME / END
    return_pct: float = 0.0          # net biaya
    r_multiple: float = 0.0
    pnl: float = 0.0


@dataclass
class _Position:
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    stop: float
    take_profit: float
    last_price: float
    entry_pos: int                   # iloc entry (untuk time stop)


@dataclass
class BacktestResult:
    metrics: Metrics
    trades: list[Trade]
    equity_curve: list[float]
    dates: list[str]
    config: BacktestConfig
    circuit_breaker_events: list[str] = field(default_factory=list)


class Backtester:
    def __init__(self, ohlcv_by_ticker: dict[str, pd.DataFrame],
                 cfg: BacktestConfig | None = None, settings=SETTINGS):
        self.cfg = cfg or BacktestConfig()
        self.settings = settings
        self.fee = self.cfg.fee_bps / 10_000.0

        # Precompute fitur (indikator kausal) sekali per ticker.
        self.feat: dict[str, pd.DataFrame] = {}
        self.pos_of_date: dict[str, dict[pd.Timestamp, int]] = {}
        for t, df in ohlcv_by_ticker.items():
            if df is None or len(df) < 210:   # butuh cukup histori (EMA200 dsb.)
                continue
            f = compute_features(df, settings.indicators)
            self.feat[t] = f
            self.pos_of_date[t] = {ts: i for i, ts in enumerate(f.index)}

        # Kalender global (union semua tanggal), terurut & difilter.
        all_dates: set[pd.Timestamp] = set()
        for f in self.feat.values():
            all_dates.update(f.index)
        dates = sorted(all_dates)
        if self.cfg.start:
            s = pd.Timestamp(self.cfg.start)
            dates = [d for d in dates if d >= s]
        if self.cfg.end:
            e = pd.Timestamp(self.cfg.end)
            dates = [d for d in dates if d <= e]
        self.dates = dates

    # -------------------------------------------------------------- helpers
    def _bar(self, ticker: str, d: pd.Timestamp) -> pd.Series | None:
        pos = self.pos_of_date[ticker].get(d)
        if pos is None:
            return None
        return self.feat[ticker].iloc[pos]

    def _lot_round(self, shares: int) -> int:
        return (shares // self.cfg.lot_size) * self.cfg.lot_size

    # ----------------------------------------------------------------- run
    def run(self) -> BacktestResult:
        cfg = self.settings.risk
        cash = self.cfg.initial_capital
        positions: dict[str, _Position] = {}
        trades: list[Trade] = []
        equity_curve: list[float] = []
        daily_returns: list[float] = []
        cb_events: list[str] = []

        prev_equity = self.cfg.initial_capital
        week_start_equity = self.cfg.initial_capital
        cur_week: tuple[int, int] | None = None
        halted_this_week = False

        for d in self.dates:
            # (0) reset breaker mingguan tiap ganti minggu ISO
            iso = d.isocalendar()
            wk = (iso[0], iso[1])
            if wk != cur_week:
                cur_week = wk
                week_start_equity = prev_equity
                halted_this_week = False

            # (1) EXIT dulu untuk posisi yang dibuka sebelum hari ini
            for ticker in list(positions.keys()):
                p = positions[ticker]
                if p.entry_date >= d:
                    continue
                bar = self._bar(ticker, d)
                if bar is None:
                    continue
                p.last_price = float(bar["close"])
                exit_price = None
                reason = ""
                if bar["low"] <= p.stop:                 # STOP prioritas
                    exit_price, reason = p.stop, "SL"
                elif bar["high"] >= p.take_profit:
                    exit_price, reason = p.take_profit, "TP"
                else:
                    held = self.pos_of_date[ticker][d] - p.entry_pos
                    if held >= self.cfg.max_hold_days:
                        exit_price, reason = float(bar["close"]), "TIME"
                if exit_price is not None:
                    cash += self._close(p, exit_price, reason, d, trades)
                    del positions[ticker]

            # (2) mark-to-market ekuitas di close hari ini
            equity = cash + sum(
                p.shares * p.last_price for p in positions.values()
            )
            equity_curve.append(equity)
            daily_returns.append(equity / prev_equity - 1.0 if prev_equity else 0.0)

            # (3) circuit breaker
            block_new = False
            daily_dd = equity / prev_equity - 1.0 if prev_equity else 0.0
            if daily_dd <= -cfg.max_daily_drawdown_pct:
                block_new = True
                cb_events.append(f"{d.date()} DAILY DD {daily_dd*100:.1f}% -> blokir entry")
            weekly_dd = equity / week_start_equity - 1.0 if week_start_equity else 0.0
            if weekly_dd <= -cfg.max_weekly_drawdown_pct:
                halted_this_week = True
                cb_events.append(f"{d.date()} WEEKLY DD {weekly_dd*100:.1f}% -> stop sisa minggu")
            block_new = block_new or halted_this_week

            # (4) ENTRY jika ada slot & tidak diblokir
            slots = cfg.max_open_positions_main - len(positions)
            if not block_new and slots > 0:
                candidates = self._rank_candidates(d, positions)
                for sc in candidates[:slots]:
                    plan = evaluate_buy(sc, equity, is_speculative=False,
                                        settings=self.settings)
                    if plan.action != "BUY":
                        continue
                    shares = self._lot_round(plan.shares)
                    if shares <= 0:
                        continue
                    cost = shares * plan.entry * (1 + self.fee)
                    if cost > cash:
                        shares = self._lot_round(int(cash / (plan.entry * (1 + self.fee))))
                        if shares <= 0:
                            continue
                        cost = shares * plan.entry * (1 + self.fee)
                    cash -= cost
                    ep = self.pos_of_date[sc.ticker][d]
                    positions[sc.ticker] = _Position(
                        ticker=sc.ticker, entry_date=d, entry_price=plan.entry,
                        shares=shares, stop=plan.stop_loss,
                        take_profit=plan.take_profit, last_price=plan.entry,
                        entry_pos=ep,
                    )

            prev_equity = equity

        # (5) tutup sisa posisi di close terakhir
        if self.dates:
            last = self.dates[-1]
            for ticker in list(positions.keys()):
                p = positions[ticker]
                bar = self._bar(ticker, last)
                px = float(bar["close"]) if bar is not None else p.last_price
                cash += self._close(p, px, "END", last, trades)
                del positions[ticker]

        returns_pct = [t.return_pct for t in trades]
        r_multiples = [t.r_multiple for t in trades]
        metrics = compute_metrics(returns_pct, r_multiples, equity_curve,
                                  daily_returns, n_days=len(self.dates))
        return BacktestResult(
            metrics=metrics, trades=trades, equity_curve=equity_curve,
            dates=[str(d.date()) for d in self.dates], config=self.cfg,
            circuit_breaker_events=cb_events,
        )

    # ----------------------------------------------------------- internals
    def _close(self, p: _Position, exit_price: float, reason: str,
               d: pd.Timestamp, trades: list[Trade]) -> float:
        """Realisasikan posisi; return kas yang diterima (net biaya jual)."""
        proceeds = p.shares * exit_price * (1 - self.fee)
        cost_basis = p.shares * p.entry_price * (1 + self.fee)
        pnl = proceeds - cost_basis
        return_pct = (pnl / cost_basis) * 100.0 if cost_basis else 0.0
        risk_per_share = p.entry_price - p.stop
        r_mult = ((exit_price - p.entry_price) / risk_per_share
                  if risk_per_share > 0 else 0.0)
        trades.append(Trade(
            ticker=p.ticker, entry_date=str(p.entry_date.date()),
            entry_price=round(p.entry_price, 2), shares=p.shares,
            stop=round(p.stop, 2), take_profit=round(p.take_profit, 2),
            exit_date=str(d.date()), exit_price=round(exit_price, 2),
            exit_reason=reason, return_pct=round(return_pct, 2),
            r_multiple=round(r_mult, 2), pnl=round(pnl, 2),
        ))
        return proceeds

    def _rank_candidates(self, d: pd.Timestamp,
                         positions: dict[str, _Position]):
        """Skor semua ticker yang punya bar di d (pakai data <= d), urut skor desc."""
        out = []
        for ticker, f in self.feat.items():
            if ticker in positions:
                continue
            pos = self.pos_of_date[ticker].get(d)
            if pos is None or pos < 210:
                continue
            sc = score_ticker(ticker, f.iloc[: pos + 1], self.settings)
            if sc is not None:
                out.append(sc)
        out.sort(key=lambda s: s.composite, reverse=True)
        return out
