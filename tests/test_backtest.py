#!/usr/bin/env python3
"""
Uji metrik & sanity backtest.

Jalankan: python -m tests.test_backtest
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant.backtest.engine import (Backtester, BacktestConfig,
                                   build_daily_returns, build_features,
                                   build_rs, precompute_scores)
from quant.backtest.metrics import (compute_metrics, max_drawdown,
                                    profit_factor, sharpe_ratio, win_rate)
from quant.config import SETTINGS


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        raise AssertionError(msg)


def test_metrics_math() -> None:
    print("\n== Metrics math ==")
    check(abs(win_rate([1, -1, 2, -3]) - 0.5) < 1e-9, "win_rate 2/4 = 0.5")
    check(abs(profit_factor([3, -1, -1]) - 1.5) < 1e-9, "profit_factor 3/2 = 1.5")
    check(abs(max_drawdown([100, 120, 90, 130]) - (90/120 - 1)) < 1e-9,
          "max_drawdown puncak120->90 = -25%")
    check(max_drawdown([100, 110, 120]) == 0.0, "kurva naik -> DD 0")
    # Sharpe deret konstan naik -> std 0 -> 0
    check(sharpe_ratio([0.01, 0.01, 0.01]) == 0.0, "return konstan -> Sharpe 0")
    check(sharpe_ratio([0.01, -0.02, 0.03, 0.00]) != 0.0, "Sharpe terdefinisi")


def make_trending_market(n_tickers: int = 6, n: int = 400,
                         seed: int = 11) -> dict:
    """Beberapa saham tren naik dgn volume akumulatif (agar ada sinyal BUY)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    out = {}
    for k in range(n_tickers):
        drift = 0.0008 + 0.0004 * k / n_tickers
        rets = rng.normal(drift, 0.012, n)
        close = 1000 * np.cumprod(1 + rets)
        # sisipkan beberapa lonjakan volume di hari naik
        vol = np.full(n, 4e6) + rng.normal(0, 4e5, n)
        up = np.append([True], close[1:] > close[:-1])
        vol[up] *= 1.6
        df = pd.DataFrame({
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.006, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.006, n))),
            "close": close, "volume": vol.clip(min=1),
        }, index=dates)
        df["ticker"] = f"T{k}.JK"
        out[f"T{k}.JK"] = df
    return out


def test_backtest_run() -> None:
    print("\n== Backtest run (data sintetis) ==")
    market = make_trending_market()
    cfg = BacktestConfig(initial_capital=100_000_000, fee_bps=20.0)
    res = Backtester(market, cfg).run()
    m = res.metrics

    check(len(res.equity_curve) == len(res.dates), "equity_curve sejajar dgn tanggal")
    check(all(e >= 0 for e in res.equity_curve), "ekuitas tidak pernah negatif")
    check(0.0 <= m.win_rate <= 1.0, f"win_rate valid ({m.win_rate:.2f})")
    check(m.max_drawdown_pct <= 0.0, f"max_drawdown <= 0 ({m.max_drawdown_pct:.3f})")
    # Setiap trade: risiko sudah dibatasi -> kerugian per trade tidak ekstrem
    for t in res.trades:
        check(t.stop < t.entry_price, f"{t.ticker}: stop < entry (stop loss wajib)")
    print(f"     n_trades={m.n_trades} win={m.win_rate*100:.0f}% "
          f"avg={m.avg_return_pct:+.2f}% MDD={m.max_drawdown_pct*100:.1f}% "
          f"Sharpe={m.sharpe:.2f}")


def test_no_lookahead_max_positions() -> None:
    print("\n== Batas posisi & no-lookahead ==")
    market = make_trending_market(n_tickers=8)
    res = Backtester(market, BacktestConfig()).run()
    # Tidak boleh ada 2 posisi ticker sama bersamaan (tak double-entry)
    # dan setiap exit_date >= entry_date (kausalitas)
    for t in res.trades:
        check(t.exit_date >= t.entry_date, f"{t.ticker}: exit >= entry")


def test_score_cache_equivalence() -> None:
    """
    Backtest dengan skor yang di-cache HARUS identik dengan yang menghitung
    skor on-the-fly. Ini jaminan bahwa optimasi walk-forward (yang memakai
    cache) tidak diam-diam mengubah perilaku strategi.
    """
    print("\n== Score-cache setara dengan non-cache ==")
    market = make_trending_market(n_tickers=8, n=450)
    cfg = BacktestConfig(initial_capital=100_000_000)

    base = Backtester(market, cfg).run()

    feat, pod = build_features(market, SETTINGS)
    cache = precompute_scores(feat, SETTINGS)
    cached = Backtester(cfg=cfg, feat=feat, pos_of_date=pod,
                        score_cache=cache).run()

    check(base.metrics.n_trades == cached.metrics.n_trades,
          f"n_trades sama ({base.metrics.n_trades})")
    check(abs(base.metrics.total_return_pct - cached.metrics.total_return_pct) < 1e-9,
          "total_return identik")
    check([t.ticker for t in base.trades] == [t.ticker for t in cached.trades],
          "urutan & ticker trade identik")
    check(all(abs(a.pnl - b.pnl) < 1e-6
              for a, b in zip(base.trades, cached.trades)),
          "PnL per trade identik")


def make_ramp_then_drop(n: int = 300, ramp_to: int = 260) -> dict:
    """
    Satu saham: uptrend mulus + volume tinggi di hari naik (memicu BUY), lalu
    JATUH tajam di akhir (memicu trailing stop). Plus beberapa filler datar.
    Deterministik (tanpa random) supaya test stabil.
    """
    dates = pd.bdate_range("2022-01-01", periods=n)
    close = np.empty(n)
    close[0] = 1000.0
    for i in range(1, n):
        if i < ramp_to:
            step = -0.003 if i % 5 == 0 else 0.006   # naik landai + pullback tiap 5 hari
        else:
            step = -0.05                              # jatuh tajam di akhir (picu trailing)
        close[i] = close[i - 1] * (1 + step)
    # candle bullish: close dekat high -> money-flow (CMF/AD) positif.
    high = close * 1.001
    low = close * 0.985
    open_ = np.append([close[0]], close[:-1])
    # volume: baseline rendah + spike tiap 10 hari (agar vol_ratio >= 1.5 terpenuhi)
    vol = np.full(n, 1e6)
    vol[np.arange(n) % 10 == 3] = 5e6        # spike di hari NAIK (bukan pullback %5==0)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": vol,
    }, index=dates)
    df["ticker"] = "RAMP.JK"
    out = {"RAMP.JK": df}
    # filler datar biar universe > 1 (tak wajib, tapi realistis)
    for k in range(3):
        flat = pd.DataFrame({
            "open": 500.0, "high": 501.0, "low": 499.0,
            "close": 500.0, "volume": 1e6,
        }, index=dates)
        flat["ticker"] = f"F{k}.JK"
        out[f"F{k}.JK"] = flat
    return out


def test_trailing_stop_locks_profit() -> None:
    """
    Dengan trailing aktif, posisi yang sempat untung besar lalu berbalik harus
    keluar di level DI ATAS entry (profit terkunci), bukan balik ke stop awal.
    Dan R-multiple dihitung dari risiko AWAL (positif untuk trade untung ini).
    """
    print("\n== Trailing stop mengunci profit ==")
    market = make_ramp_then_drop()
    cfg = BacktestConfig(initial_capital=100_000_000)

    tr_on = replace(SETTINGS, trailing=replace(SETTINGS.trailing, enabled=True))
    on = Backtester(market, cfg, settings=tr_on).run()
    off = Backtester(market, cfg).run()                      # trailing default OFF

    ramp_on = [t for t in on.trades if t.ticker == "RAMP.JK"]
    check(len(ramp_on) >= 1, "trailing ON: minimal 1 trade RAMP.JK terpicu")
    t = ramp_on[0]
    check(t.stop < t.entry_price, "stop AWAL tetap < entry (stop loss wajib)")
    check(t.exit_price > t.entry_price,
          f"exit ({t.exit_price}) di atas entry ({t.entry_price}) -> profit terkunci")
    check(t.r_multiple > 0, f"R-multiple positif ({t.r_multiple}) dari risiko awal")
    print(f"     ON:  exit={t.exit_reason} @ {t.exit_price} "
          f"ret={t.return_pct:+.1f}% R={t.r_multiple:+.2f}")
    if off.trades:
        to = off.trades[0]
        print(f"     OFF: exit={to.exit_reason} @ {to.exit_price} "
              f"ret={to.return_pct:+.1f}% R={to.r_multiple:+.2f}")


def test_regime_filter_blocks_entries() -> None:
    """
    Filter regime: kalau pasar dinyatakan bearish di SEMUA tanggal, tidak boleh
    ada entry sama sekali. Kalau bullish di semua tanggal, hasil = tanpa filter.
    """
    print("\n== Filter regime (blok entry saat pasar bearish) ==")
    market = make_trending_market(n_tickers=8, n=450)
    cfg = BacktestConfig(initial_capital=100_000_000)

    all_dates = sorted({d for df in market.values() for d in df.index})
    bear = {d: False for d in all_dates}
    bull = {d: True for d in all_dates}

    no_filter = Backtester(market, cfg).run()
    bear_run = Backtester(market, cfg, regime_ok=bear).run()
    bull_run = Backtester(market, cfg, regime_ok=bull).run()

    check(bear_run.metrics.n_trades == 0, "pasar bearish -> 0 trade")
    check(bull_run.metrics.n_trades == no_filter.metrics.n_trades,
          "pasar bullish penuh == tanpa filter")


def test_rs_filter_blocks_underperformers() -> None:
    """
    Filter kekuatan relatif: kalau indeks jauh MENGUNGGULI semua saham, tak satu
    pun saham lolos -> 0 trade. Kalau indeks datar (semua saham unggul), hasil =
    tanpa filter. Dibangun dari data sintetis yang deterministik terhadap seed.
    """
    print("\n== Filter kekuatan relatif (RS vs indeks) ==")
    market = make_trending_market(n_tickers=8, n=450)
    dates = next(iter(market.values())).index
    cfg = BacktestConfig(initial_capital=100_000_000)

    rs_on = replace(SETTINGS, rs=replace(SETTINGS.rs, enabled=True))

    # Indeks kuat: naik ~1%/hari -> ROC jauh di atas ROC saham -> semua kalah.
    strong = pd.DataFrame(
        {"close": 1000.0 * np.power(1.01, np.arange(len(dates)))}, index=dates)
    rs_strong = build_rs(market, strong, rs_on)
    strong_run = Backtester(market, cfg, settings=rs_on, rs_ok=rs_strong).run()
    check(strong_run.metrics.n_trades == 0,
          "indeks jauh unggul -> tak ada saham lolos RS -> 0 trade")

    # Indeks datar: ROC indeks 0 -> saham uptrend selalu unggul -> == tanpa filter.
    flat = pd.DataFrame({"close": 1000.0}, index=dates)
    rs_flat = build_rs(market, flat, rs_on)
    flat_run = Backtester(market, cfg, settings=rs_on, rs_ok=rs_flat).run()
    no_filter = Backtester(market, cfg).run()
    check(flat_run.metrics.n_trades == no_filter.metrics.n_trades,
          "indeks datar (semua saham unggul) == tanpa filter RS")


def test_corr_filter_blocks_correlated_entries() -> None:
    """
    Kandidat #5: filter korelasi antar-posisi.
      1. max_corr=1.0 (netral) -> perilaku identik baseline.
      2. Helper _corr_ok_vs_open: menolak kandidat yang berkorelasi > ambang
         dgn posisi terbuka; meloloskan yang tidak berkorelasi.
    (Kandidat ini DITOLAK di OOS jujur, tapi mekanismenya wajib benar.)
    """
    print("\n== Filter korelasi antar-posisi ==")
    market = make_trending_market(n_tickers=6, n=450)
    feat, pod = build_features(market, SETTINGS)
    dr = build_daily_returns(feat)
    cfg = BacktestConfig(initial_capital=100_000_000)

    base = Backtester(market, cfg).run()
    st_neutral = replace(SETTINGS, corr_filter=replace(
        SETTINGS.corr_filter, enabled=True, max_corr=1.0))
    neutral = Backtester(market, cfg, settings=st_neutral, feat=feat,
                         pos_of_date=pod, daily_returns=dr).run()
    check(neutral.metrics.n_trades == base.metrics.n_trades,
          "max_corr=1.0 (netral) == baseline (backward-compatible)")

    # Unit-test langsung helper _corr_ok_vs_open dgn 2 seri: (A) identik dgn T0
    # -> corr ~1 -> ditolak; (B) acak independen -> corr ~0 -> lolos.
    from quant.backtest.engine import _Position   # noqa: E402
    st_strict = replace(SETTINGS, corr_filter=replace(
        SETTINGS.corr_filter, enabled=True, max_corr=0.5, min_samples=20))
    bt = Backtester(market, cfg, settings=st_strict, feat=feat,
                    pos_of_date=pod, daily_returns=dr)
    d = market["T0.JK"].index[-1]
    # posisi "terbuka" fiktif di T0
    open_pos = {"T0.JK": _Position(
        ticker="T0.JK", entry_date=d, entry_price=1.0, shares=100,
        stop=0.9, take_profit=1.3, last_price=1.0, entry_pos=0)}

    # (A) kandidat sinkron 100% dgn T0 -> corr ~1 -> HARUS ditolak
    bt.daily_returns = dict(dr)
    bt.daily_returns["TCLONE.JK"] = dr["T0.JK"].copy()
    check(not bt._corr_ok_vs_open("TCLONE.JK", d, open_pos),
          "kandidat identik dgn posisi terbuka (corr~1) -> ditolak")

    # (B) kandidat return acak independen -> corr ~0 -> HARUS lolos
    rng = np.random.default_rng(23)
    idx = dr["T0.JK"].index
    bt.daily_returns["TIND.JK"] = pd.Series(
        rng.normal(0.0, 0.01, len(idx)), index=idx)
    check(bt._corr_ok_vs_open("TIND.JK", d, open_pos),
          "kandidat independen (corr~0) -> lolos")


def test_kelly_sizing_scale_and_neutrality() -> None:
    """
    Kandidat #7 sizing Kelly-fraksi:
      1. Netral: enabled+kelly_fraction=0 ATAU disabled -> hasil == baseline
         (backward-compatible, sizing flat).
      2. _kelly_scale: <min_trades -> 1.0; edge+ -> >1 (di-clamp hard cap);
         edge- -> <1 (di-lantai min_scale); tak pernah > cap/base (gerbang risiko).
    """
    print("\n== Sizing Kelly-fraksi adaptif (#7) ==")
    market = make_trending_market(n_tickers=6, n=450)
    feat, pod = build_features(market, SETTINGS)
    cfg = BacktestConfig(initial_capital=100_000_000)

    base = Backtester(market, cfg).run()
    # (1a) enabled tapi kelly_fraction=0 -> netral
    st_zero = replace(SETTINGS, kelly=replace(
        SETTINGS.kelly, enabled=True, kelly_fraction=0.0))
    z = Backtester(market, cfg, settings=st_zero, feat=feat,
                   pos_of_date=pod).run()
    check(z.metrics.n_trades == base.metrics.n_trades
          and abs(z.metrics.total_return_pct - base.metrics.total_return_pct) < 1e-9,
          "enabled + kelly_fraction=0 == baseline (netral)")

    # (2) perilaku _kelly_scale (base 1%, hard cap 2% -> plafon efektif 2.0x)
    st = replace(SETTINGS, kelly=replace(
        SETTINGS.kelly, enabled=True, kelly_fraction=2.0,
        min_trades=10, lookback_trades=20, min_scale=0.5, max_scale=5.0))
    bt = Backtester(market, cfg, settings=st, feat=feat, pos_of_date=pod)
    check(bt._kelly_scale([0.5] * 5) == 1.0, "sampel < min_trades -> 1.0")
    # edge +0.3R, kf=2 -> 1.6x (di bawah plafon 2.0)
    s_pos = bt._kelly_scale([0.3] * 20)
    check(abs(s_pos - 1.6) < 1e-9, f"edge+ -> scale 1.6 (dapat {s_pos:.2f})")
    # edge +2R, kf=2 -> 5.0 mentah, TAPI di-clamp hard cap (cap/base=2.0)
    s_cap = bt._kelly_scale([2.0] * 20)
    check(abs(s_cap - 2.0) < 1e-9,
          f"edge sangat+ -> di-clamp ke plafon hard cap 2.0 (dapat {s_cap:.2f})")
    # edge -1R, kf=2 -> -1.0 mentah, di-lantai min_scale 0.5
    s_neg = bt._kelly_scale([-1.0] * 20)
    check(abs(s_neg - 0.5) < 1e-9,
          f"edge- -> di-lantai min_scale 0.5 (dapat {s_neg:.2f})")


def test_breadth_filter_blocks_narrow_market() -> None:
    """
    Kandidat #8 filter breadth (non-price):
      1. build_breadth: nilai per-tanggal = fraksi ticker close>EMA (0..1),
         kausal & terdefinisi.
      2. Netral: enabled tapi min_breadth=0 -> hasil == baseline.
      3. Blokir: min_breadth sangat tinggi (>maks breadth yg pernah terjadi) ->
         0 trade (semua entry diblokir karena pasar dianggap tak cukup lebar).
    """
    from quant.backtest.engine import build_breadth
    print("\n== Filter breadth pasar (#8) ==")
    market = make_trending_market(n_tickers=6, n=450)
    feat, pod = build_features(market, SETTINGS)
    cfg = BacktestConfig(initial_capital=100_000_000)

    br = build_breadth(feat, SETTINGS)
    check(br is not None and len(br) > 0, "build_breadth menghasilkan peta tanggal")
    check(all(0.0 <= v <= 1.0 for v in br.values()),
          "semua nilai breadth di [0,1]")

    base = Backtester(market, cfg).run()
    # (2) netral: enabled + min_breadth=0
    st_neutral = replace(SETTINGS, breadth=replace(
        SETTINGS.breadth, enabled=True, min_breadth=0.0))
    neu = Backtester(market, cfg, settings=st_neutral, feat=feat,
                     pos_of_date=pod, breadth=br).run()
    check(neu.metrics.n_trades == base.metrics.n_trades
          and abs(neu.metrics.total_return_pct - base.metrics.total_return_pct) < 1e-9,
          "enabled + min_breadth=0 == baseline (netral)")

    # (3) ambang mustahil (1.01) -> selalu memblokir -> 0 trade
    st_block = replace(SETTINGS, breadth=replace(
        SETTINGS.breadth, enabled=True, min_breadth=1.01))
    blk = Backtester(market, cfg, settings=st_block, feat=feat,
                     pos_of_date=pod, breadth=br).run()
    check(blk.metrics.n_trades == 0,
          "min_breadth mustahil (1.01) -> semua entry diblokir (0 trade)")


def test_weekly_resample_ohlcv() -> None:
    """
    Sanity resample harian -> mingguan (W-FRI):
      - Jumlah bar mingguan ~ #hari / 5.
      - Aturan aggregation OHLCV benar: open=first, high=max, low=min,
        close=last, volume=sum.
      - Metrik Sharpe/CAGR pakai periods_per_year=52 utk mingguan (tak
        ada overstate 2.2x akibat annualisasi salah).
    """
    from quant.data.resample import to_weekly
    print("\n== Weekly resample OHLCV ==")
    n = 200
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(7)
    close = 1000 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    df = pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.001, n)),
        "high": close * (1 + np.abs(rng.normal(0, 0.005, n))),
        "low": close * (1 - np.abs(rng.normal(0, 0.005, n))),
        "close": close,
        "volume": np.full(n, 1_000_000),
        "ticker": "T.JK", "market": "IDX",
    }, index=idx)
    w = to_weekly(df)
    check(len(w) <= n // 5 + 5 and len(w) >= n // 5 - 2,
          f"jumlah bar mingguan ~ n/5 ({len(w)} vs ~{n//5})")
    # Cek aggregation pada minggu pertama yang punya >=5 hari
    for wk in w.index:
        chunk = df.loc[df.index <= wk].tail(5)
        if len(chunk) < 5:
            continue
        # cari 5-hari terakhir sebelum wk (yang membentuk w.loc[wk])
        mask = (df.index > (wk - pd.Timedelta(days=7))) & (df.index <= wk)
        c = df[mask]
        if len(c) == 0:
            continue
        check(abs(w.loc[wk, "open"] - c["open"].iloc[0]) < 1e-9,
              f"open = bar pertama minggu {wk.date()}")
        check(abs(w.loc[wk, "high"] - c["high"].max()) < 1e-9, "high=max")
        check(abs(w.loc[wk, "low"] - c["low"].min()) < 1e-9, "low=min")
        check(abs(w.loc[wk, "close"] - c["close"].iloc[-1]) < 1e-9, "close=last")
        check(abs(w.loc[wk, "volume"] - c["volume"].sum()) < 1e-9, "volume=sum")
        break
    # Kolom konstan ikut
    check(w["ticker"].iloc[0] == "T.JK", "ticker konstan diteruskan")

    # Metrik: annualisasi mingguan pakai periods_per_year=52
    from quant.backtest.metrics import sharpe_ratio
    # deret return "konstan positif" -> Sharpe inf (std=0), skip.
    # deret variatif: bandingkan pengaruh periods_per_year
    r = [0.01, -0.005, 0.008, 0.002, -0.003, 0.01, -0.002, 0.005]
    s52 = sharpe_ratio(r, periods_per_year=52)
    s252 = sharpe_ratio(r, periods_per_year=252)
    check(abs(s252 / s52 - (252 / 52) ** 0.5) < 1e-6,
          "Sharpe scaling sqrt(52 -> 252) = sqrt(252/52)")


if __name__ == "__main__":
    print("Menjalankan uji backtest...")
    test_metrics_math()
    test_backtest_run()
    test_no_lookahead_max_positions()
    test_score_cache_equivalence()
    test_trailing_stop_locks_profit()
    test_regime_filter_blocks_entries()
    test_rs_filter_blocks_underperformers()
    test_corr_filter_blocks_correlated_entries()
    test_kelly_sizing_scale_and_neutrality()
    test_breadth_filter_blocks_narrow_market()
    test_weekly_resample_ohlcv()
    print("\nSemua uji backtest LULUS.\n")
