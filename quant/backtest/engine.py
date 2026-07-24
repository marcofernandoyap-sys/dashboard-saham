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

from quant.analysis import indicators as ind
from quant.analysis.scoring import Score, compute_features, score_ticker
from quant.analysis.signals import evaluate_buy
from quant.backtest.metrics import Metrics, compute_metrics
from quant.config import SETTINGS

MIN_HISTORY = 210   # butuh cukup histori (EMA200 dsb.) sebelum boleh skor/entry


def build_regime(index_df: pd.DataFrame | None,
                 settings=SETTINGS) -> dict[pd.Timestamp, bool] | None:
    """
    Peta {tanggal -> pasar boleh long?} berdasarkan close indeks > EMA(period).

    KAUSAL: EMA di tanggal d hanya memakai data <= d, konsisten dgn sinyal lain
    (tak ada lookahead). Return None kalau data indeks tidak ada/kurang -> engine
    memperlakukannya sebagai 'tidak ada filter' agar tidak diam-diam memblokir semua.
    """
    rp = settings.regime
    if index_df is None or len(index_df) < rp.ema_period:
        return None
    ema = ind.ema(index_df["close"], rp.ema_period)
    ok = index_df["close"] > ema
    return {ts: bool(v) for ts, v in ok.items()}


def build_rs(ohlcv_by_ticker: dict[str, pd.DataFrame],
             index_df: pd.DataFrame | None,
             settings=SETTINGS) -> dict[str, dict[pd.Timestamp, bool]] | None:
    """
    Peta {ticker -> {tanggal -> saham unggul indeks?}} berbasis selisih
    rate-of-change: ROC_saham(lookback) - ROC_indeks(lookback) > min_rs.

    KAUSAL: ROC di tanggal d hanya pakai close(d) & close(d-lookback) -> tanpa
    lookahead. ROC indeks diselaraskan ke kalender tiap ticker (ffill utk jaga2
    hari libur yang berbeda). Return None kalau data indeks tidak ada/kurang
    (engine memperlakukannya sebagai 'tidak ada filter'). Peta dibangun terlepas
    dari flag enabled; engine yang memutuskan menerapkannya atau tidak.
    """
    rp = settings.rs
    if index_df is None or len(index_df) <= rp.lookback:
        return None
    idx_roc = index_df["close"] / index_df["close"].shift(rp.lookback) - 1.0
    out: dict[str, dict[pd.Timestamp, bool]] = {}
    for t, df in ohlcv_by_ticker.items():
        if df is None or len(df) <= rp.lookback:
            continue
        stock_roc = df["close"] / df["close"].shift(rp.lookback) - 1.0
        aligned_idx = idx_roc.reindex(df.index).ffill()
        rel = stock_roc - aligned_idx
        out[t] = {ts: bool(v > rp.min_rs) for ts, v in rel.items()
                  if pd.notna(v)}
    return out


def build_vol_rank(feat: dict[str, pd.DataFrame],
                   settings=SETTINGS) -> dict[str, dict[pd.Timestamp, float]]:
    """
    Peta {ticker -> {tanggal -> peringkat persentil ATR% (0..1)}} untuk kandidat
    #4A (filter volatilitas entry).

    ATR% = atr/close. Peringkat = fraksi nilai pada jendela `atr_pct_window`
    (sampai & termasuk hari d) yang <= ATR% hari d. 1.0 = paling bergejolak dalam
    jendela.

    KAUSAL: rolling window berakhir di d (tak melihat masa depan). Dibangun dari
    `feat` (fitur yang sudah dihitung), jadi murah & tak menyentuh skor komposit.
    """
    w = settings.vol_filter.atr_pct_window
    out: dict[str, dict[pd.Timestamp, float]] = {}
    for t, f in feat.items():
        if f is None or f.empty or "atr" not in f.columns:
            continue
        atr_pct = (f["atr"] / f["close"]).replace([float("inf")], pd.NA)
        # peringkat persentil rolling KAUSAL: rank hari terakhir dalam jendela.
        rank = atr_pct.rolling(w, min_periods=max(20, w // 5)).apply(
            lambda a: (a <= a[-1]).mean(), raw=True)
        out[t] = {ts: float(v) for ts, v in rank.items() if pd.notna(v)}
    return out


def build_breadth(feat: dict[str, pd.DataFrame],
                  settings=SETTINGS) -> dict[pd.Timestamp, float] | None:
    """
    Peta {tanggal -> lebar pasar} = fraksi ticker universe dgn close > EMA(period)
    pada tanggal itu, untuk kandidat #8 (filter breadth).

    KAUSAL: tiap sel (close > ema) hanya memakai data tanggal d -> tanpa lookahead.
    Dibangun dari `feat` (fitur cache), jadi murah & tak menyentuh skor komposit.
    Threshold TIDAK di-bake di sini (engine yang membandingkan dgn min_breadth),
    supaya peta ini bisa dipakai ulang lintas fold walau ambang di-tuning.

    Return None kalau kolom EMA tak tersedia -> engine memperlakukan 'tak ada
    filter' (tidak diam-diam memblokir semua).
    """
    col = f"ema_{settings.breadth.ema_period}"
    parts: list[pd.Series] = []
    for t, f in feat.items():
        if f is None or f.empty or col not in f.columns or "close" not in f.columns:
            continue
        valid = f[col].notna()
        # True/False di mana EMA valid, NaN di mana belum (di-skip saat rata2).
        parts.append((f["close"] > f[col]).where(valid).rename(t))
    if not parts:
        return None
    mat = pd.concat(parts, axis=1)
    breadth = mat.mean(axis=1, skipna=True)     # fraksi True di antara yang valid
    return {ts: float(v) for ts, v in breadth.items() if pd.notna(v)}


def build_daily_returns(feat: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    """
    Peta {ticker -> pd.Series return harian close-to-close} untuk kandidat #5
    (filter korelasi antar-posisi).

    KAUSAL: return d dihitung dari close(d) & close(d-1) — sudah diketahui saat
    sinyal muncul di close d. Series diindeks tanggal dan dipakai on-the-fly di
    entry loop utk pairwise correlation dgn posisi yang SUDAH terbuka.
    """
    out: dict[str, pd.Series] = {}
    for t, f in feat.items():
        if f is None or f.empty or "close" not in f.columns:
            continue
        out[t] = f["close"].pct_change()
    return out


def build_features(ohlcv_by_ticker: dict[str, pd.DataFrame],
                   settings=SETTINGS) -> tuple[dict, dict]:
    """Hitung fitur indikator kausal sekali per ticker (mahal, cache-able)."""
    feat: dict[str, pd.DataFrame] = {}
    pos_of_date: dict[str, dict[pd.Timestamp, int]] = {}
    for t, df in ohlcv_by_ticker.items():
        if df is None or len(df) < MIN_HISTORY:
            continue
        f = compute_features(df, settings.indicators)
        feat[t] = f
        pos_of_date[t] = {ts: i for i, ts in enumerate(f.index)}
    return feat, pos_of_date


def precompute_scores(feat: dict[str, pd.DataFrame],
                      settings=SETTINGS) -> dict[str, dict[pd.Timestamp, Score]]:
    """
    Skor komposit tiap (ticker, tanggal) SATU KALI.

    Skor komposit + confirming_categories + volume_confirmed + above_ema_trend
    TIDAK bergantung pada parameter yang di-tuning grid-search
    (buy_score_threshold / atr_stop_mult / min_risk_reward). Jadi cache ini bisa
    dipakai ulang lintas fold & lintas kombinasi parameter -> grid-search murah.

    CATATAN: cache HANYA valid selama volume_confirm_mult & require_above_ema
    (yang ter-"bake" ke dalam field Score) tetap. Kalau keduanya ikut di-tuning,
    cache harus dibangun ulang.
    """
    cache: dict[str, dict[pd.Timestamp, Score]] = {}
    for ticker, f in feat.items():
        by_date: dict[pd.Timestamp, Score] = {}
        idx = f.index
        for pos in range(MIN_HISTORY, len(f)):
            sc = score_ticker(ticker, f.iloc[: pos + 1], settings)
            if sc is not None:
                by_date[idx[pos]] = sc
        cache[ticker] = by_date
    return cache


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000_000.0
    fee_bps: float = 20.0            # 0.20% per sisi
    lot_size: int = 100              # 1 lot IDX = 100 lembar
    max_hold_days: int = 60          # time stop (hindari posisi menggantung)
    start: str | None = None
    end: str | None = None
    # Faktor anualisasi Sharpe/CAGR: 252 = harian, 52 = mingguan.
    # Kalau bar OHLCV bukan harian, wajib override supaya metrik tak keliru.
    periods_per_year: int = 252


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
    stop: float                      # stop berjalan (bisa di-ratchet naik oleh trailing)
    take_profit: float
    last_price: float
    entry_pos: int                   # iloc entry (untuk time stop)
    initial_risk: float = 0.0        # entry - stop awal (untuk ambang aktivasi trailing)
    high_since: float = 0.0          # harga tertinggi sejak entry (untuk chandelier)
    scaled_out: bool = False         # kandidat #4C: sudah ambil sebagian?


@dataclass
class BacktestResult:
    metrics: Metrics
    trades: list[Trade]
    equity_curve: list[float]
    dates: list[str]
    config: BacktestConfig
    circuit_breaker_events: list[str] = field(default_factory=list)


class Backtester:
    def __init__(self, ohlcv_by_ticker: dict[str, pd.DataFrame] | None = None,
                 cfg: BacktestConfig | None = None, settings=SETTINGS, *,
                 feat: dict | None = None, pos_of_date: dict | None = None,
                 score_cache: dict | None = None,
                 regime_ok: dict | None = None,
                 rs_ok: dict | None = None,
                 vol_rank: dict | None = None,
                 daily_returns: dict | None = None,
                 breadth: dict | None = None):
        """
        Dua cara pakai:
          1. Berikan `ohlcv_by_ticker` -> fitur dihitung di sini (jalur biasa).
          2. Berikan `feat`+`pos_of_date` (+opsional `score_cache`) hasil
             build_features/precompute_scores -> dipakai ulang lintas fold &
             parameter tanpa hitung ulang (jalur walk-forward yang murah).
        """
        self.cfg = cfg or BacktestConfig()
        self.settings = settings
        self.fee = self.cfg.fee_bps / 10_000.0
        self.score_cache = score_cache
        self.regime_ok = regime_ok
        self.rs_ok = rs_ok
        self.vol_rank = vol_rank
        self.daily_returns = daily_returns
        self.breadth = breadth

        if feat is not None and pos_of_date is not None:
            self.feat = feat
            self.pos_of_date = pos_of_date
        else:
            self.feat, self.pos_of_date = build_features(
                ohlcv_by_ticker or {}, settings)

        # Auto-bangun breadth di jalur langsung (tanpa cache eksternal) HANYA
        # kalau diaktifkan -> baseline (nonaktif) tetap tak menghitung apa pun.
        if self.breadth is None and self.settings.breadth.enabled:
            self.breadth = build_breadth(self.feat, self.settings)

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

    def _kelly_scale(self, closed_r: list[float]) -> float:
        """
        Kandidat #7: faktor skala sizing dari expektansi R bergulir (kausal).

        `closed_r` = r_multiple trade yang SUDAH ditutup sampai sebelum entry
        ini (dibangun dari `trades`, yang exit-nya diproses lebih dulu di hari
        yang sama -> bukan lookahead). Return 1.0 (netral) kalau nonaktif,
        kelly_fraction 0, atau sampel belum cukup.
        """
        ks = self.settings.kelly
        if not ks.enabled or ks.kelly_fraction == 0.0:
            return 1.0
        if len(closed_r) < ks.min_trades:
            return 1.0
        window = closed_r[-ks.lookback_trades:]
        e_r = sum(window) / len(window)          # expektansi R terkini
        scale = 1.0 + ks.kelly_fraction * e_r    # >1 saat edge+, <1 saat edge-
        # GERBANG RISIKO: base_risk * scale tak boleh lewati hard cap.
        base = self.settings.risk.max_risk_per_trade_pct
        cap = self.settings.risk.max_risk_per_trade_hard_cap_pct
        hi = min(ks.max_scale, cap / base if base > 0 else ks.max_scale)
        return float(min(max(scale, ks.min_scale), hi))

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

            # (1) EXIT dulu untuk posisi yang dibuka sebelum hari ini.
            # Stop hari ini = level dari data <= kemarin (p.stop sudah di-ratchet
            # di akhir pemrosesan hari sebelumnya) -> tanpa lookahead.
            tr = self.settings.trailing
            use_tp = not (tr.enabled and not tr.use_take_profit_cap)
            for ticker in list(positions.keys()):
                p = positions[ticker]
                if p.entry_date >= d:
                    continue
                bar = self._bar(ticker, d)
                if bar is None:
                    continue
                p.last_price = float(bar["close"])
                # (1a) STOP prioritas -> exit penuh (paling konservatif).
                if bar["low"] <= p.stop:
                    cash += self._close(p, p.stop, "SL", d, trades)
                    del positions[ticker]
                    continue
                # (1a') Kandidat #4C: partial scale-out di trigger_r (kausal via
                # high hari ini, sama seperti take-profit). Sisanya tetap jalan;
                # stop dinaikkan ke breakeven.
                so = self.settings.scaleout
                if so.enabled and not p.scaled_out and p.initial_risk > 0:
                    trigger = p.entry_price + so.trigger_r * p.initial_risk
                    if bar["high"] >= trigger:
                        sold = self._lot_round(int(p.shares * so.fraction))
                        if 0 < sold < p.shares:
                            cash += self._realize(p, sold, trigger, "SCALE",
                                                  d, trades)
                            p.shares -= sold
                            p.scaled_out = True
                            if (so.move_stop_breakeven
                                    and p.stop < p.entry_price):
                                p.stop = p.entry_price
                # (1b) TAKE PROFIT penuh (sisa).
                if use_tp and bar["high"] >= p.take_profit:
                    cash += self._close(p, p.take_profit, "TP", d, trades)
                    del positions[ticker]
                    continue
                # (1c) TIME stop.
                held = self.pos_of_date[ticker][d] - p.entry_pos
                if held >= self.cfg.max_hold_days:
                    cash += self._close(p, float(bar["close"]), "TIME", d, trades)
                    del positions[ticker]
                    continue
                # (1b) ratchet trailing stop dari data hari ini -> berlaku besok.
                if tr.enabled:
                    p.high_since = max(p.high_since, float(bar["high"]))
                    atr_now = float(bar["atr"]) if pd.notna(bar.get("atr")) else 0.0
                    activated = (p.high_since - p.entry_price
                                 >= tr.activate_r * p.initial_risk)
                    if activated and atr_now > 0:
                        candidate = p.high_since - tr.trail_atr_mult * atr_now
                        if candidate > p.stop:           # HANYA naik (ratchet)
                            p.stop = candidate

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

            # (3b) filter regime: pasar harus bullish (indeks > EMA) untuk long.
            # Kalau tak ada peta regime (data indeks absen) -> tidak memblokir.
            if (self.settings.regime.enabled and self.regime_ok is not None
                    and not self.regime_ok.get(d, False)):
                block_new = True

            # (3c) filter breadth (#8): butuh partisipasi universe cukup lebar.
            # Kalau tak ada peta breadth -> tidak memblokir. min_breadth=0 = off.
            # .get(d, 1.0): tanggal tak terhitung dianggap sehat (tak memblokir).
            bp = self.settings.breadth
            if (bp.enabled and bp.min_breadth > 0.0 and self.breadth is not None
                    and self.breadth.get(d, 1.0) < bp.min_breadth):
                block_new = True

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
                    # Kandidat #7: sizing Kelly-fraksi adaptif. Skala lembar dari
                    # expektansi R bergulir (kausal: `trades` sudah termasuk exit
                    # hari ini, diproses di atas). r_multiple tak tergantung
                    # jumlah lembar -> sizing tak mengganggu sinyal edge. Netral
                    # (scale=1) saat kelly nonaktif -> baseline tak berubah.
                    kscale = self._kelly_scale([t.r_multiple for t in trades])
                    if kscale != 1.0:
                        shares = self._lot_round(int(shares * kscale))
                        if shares <= 0:
                            continue
                    # Kandidat #5: filter korelasi antar-posisi (kausal, cek vs
                    # posisi yang SUDAH terbuka termasuk yang baru dibuka hari ini).
                    if not self._corr_ok_vs_open(sc.ticker, d, positions):
                        continue
                    # Kandidat #4B: cap bobot notional posisi thd ekuitas
                    # (batasi konsentrasi). 1.0 = off.
                    mpw = cfg.max_position_weight
                    if mpw < 1.0 and plan.entry > 0:
                        max_notional = equity * mpw
                        if shares * plan.entry > max_notional:
                            shares = self._lot_round(int(max_notional / plan.entry))
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
                        initial_risk=max(plan.entry - plan.stop_loss, 0.0),
                        high_since=plan.entry,
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
                                  daily_returns, n_days=len(self.dates),
                                  periods_per_year=self.cfg.periods_per_year)
        return BacktestResult(
            metrics=metrics, trades=trades, equity_curve=equity_curve,
            dates=[str(d.date()) for d in self.dates], config=self.cfg,
            circuit_breaker_events=cb_events,
        )

    # ----------------------------------------------------------- internals
    def _realize(self, p: _Position, shares: int, exit_price: float,
                 reason: str, d: pd.Timestamp, trades: list[Trade]) -> float:
        """
        Realisasikan `shares` lembar dari posisi p pada exit_price; catat 1 Trade
        & return kas yang diterima (net biaya jual). Dipakai untuk exit penuh
        MAUPUN partial scale-out (tiap potongan jadi baris trade sendiri).
        """
        proceeds = shares * exit_price * (1 - self.fee)
        cost_basis = shares * p.entry_price * (1 + self.fee)
        pnl = proceeds - cost_basis
        return_pct = (pnl / cost_basis) * 100.0 if cost_basis else 0.0
        # R-multiple SELALU berbasis risiko AWAL (stop loss wajib saat entry),
        # bukan stop yang sudah di-trail (kalau tidak, R jadi menyesatkan).
        risk_per_share = (p.initial_risk if p.initial_risk > 0
                          else p.entry_price - p.stop)
        r_mult = ((exit_price - p.entry_price) / risk_per_share
                  if risk_per_share > 0 else 0.0)
        initial_stop = p.entry_price - risk_per_share
        trades.append(Trade(
            ticker=p.ticker, entry_date=str(p.entry_date.date()),
            entry_price=round(p.entry_price, 2), shares=shares,
            stop=round(initial_stop, 2), take_profit=round(p.take_profit, 2),
            exit_date=str(d.date()), exit_price=round(exit_price, 2),
            exit_reason=reason, return_pct=round(return_pct, 2),
            r_multiple=round(r_mult, 2), pnl=round(pnl, 2),
        ))
        return proceeds

    def _close(self, p: _Position, exit_price: float, reason: str,
               d: pd.Timestamp, trades: list[Trade]) -> float:
        """Realisasikan SELURUH sisa posisi."""
        return self._realize(p, p.shares, exit_price, reason, d, trades)

    def _corr_ok_vs_open(self, ticker: str, d: pd.Timestamp,
                         positions: dict[str, _Position]) -> bool:
        """
        Kandidat #5: True kalau korelasi kandidat vs SEMUA posisi terbuka masih
        di bawah ambang. Kausal: pakai return harian jendela lookback berakhir
        di d (close d sudah diketahui). Kalau tak ada peta / <min_samples ->
        LOLOS (jangan diam-diam menolak semua).
        """
        cf = self.settings.corr_filter
        if not cf.enabled or cf.max_corr >= 1.0 or self.daily_returns is None:
            return True
        if not positions:
            return True
        r_new = self.daily_returns.get(ticker)
        if r_new is None:
            return True
        r_new = r_new.loc[:d].tail(cf.lookback)
        for open_t in positions:
            r_o = self.daily_returns.get(open_t)
            if r_o is None:
                continue
            r_o = r_o.loc[:d].tail(cf.lookback)
            joined = pd.concat([r_new, r_o], axis=1, join="inner").dropna()
            if len(joined) < cf.min_samples:
                continue
            c = joined.iloc[:, 0].corr(joined.iloc[:, 1])
            if pd.notna(c) and c > cf.max_corr:
                return False
        return True

    def _rank_candidates(self, d: pd.Timestamp,
                         positions: dict[str, _Position]):
        """Skor semua ticker yang punya bar di d (pakai data <= d), urut skor desc."""
        out = []
        for ticker, f in self.feat.items():
            if ticker in positions:
                continue
            pos = self.pos_of_date[ticker].get(d)
            if pos is None or pos < MIN_HISTORY:
                continue
            # Filter kekuatan relatif: hanya saham yang unggul indeks. Kalau tak
            # ada peta RS (data indeks absen) -> tidak memblokir.
            if (self.settings.rs.enabled and self.rs_ok is not None
                    and not self.rs_ok.get(ticker, {}).get(d, False)):
                continue
            # Kandidat #4A: filter volatilitas — tolak entry saat ATR% saham di
            # persentil tinggi. Kalau peta tak ada -> tidak memblokir.
            vf = self.settings.vol_filter
            if (vf.enabled and vf.max_percentile < 1.0
                    and self.vol_rank is not None):
                rk = self.vol_rank.get(ticker, {}).get(d)
                if rk is not None and rk > vf.max_percentile:
                    continue
            if self.score_cache is not None:
                sc = self.score_cache.get(ticker, {}).get(d)
            else:
                sc = score_ticker(ticker, f.iloc[: pos + 1], self.settings)
            if sc is not None:
                out.append(sc)
        out.sort(key=lambda s: s.composite, reverse=True)
        return out
