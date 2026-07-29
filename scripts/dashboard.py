#!/usr/bin/env python3
"""
Dashboard READ-ONLY (Fase 4) — Streamlit.

Jalankan:
    streamlit run scripts/dashboard.py

Prinsip (sesuai spec proyek):
  - MURNI baca & tampilkan. TIDAK ada tombol eksekusi order di mana pun.
  - Gerbang kesiapan live + status regime pasar ditampilkan MENONJOL supaya
    keputusan manusia sadar konteks risiko.
  - Semua angka berasal dari modul analisa/backtest yang sama dengan CLI
    (satu sumber kebenaran) — dashboard tidak menghitung ulang logika sendiri.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from quant.analysis.decision import decide
from quant.analysis.footprint import compute_footprint
from quant.analysis.scoring import compute_features, score_ticker
from quant.analysis.screener import detect_speculative, screen_liquidity_idx
from quant.analysis.signals import evaluate_buy
from quant.backtest.engine import build_regime, build_rs
from quant.backtest.registry import live_readiness, load_latest
from quant.config import SETTINGS
from quant.data.storage import Storage
from quant.universe import LQ45

# Universe yang sudah lolos walk-forward = LQ45 (edge 0.54 divalidasi di sini).
# Nama di luar ini adalah "extended" — belum divalidasi OOS, perlakukan hati-hati.
_VALIDATED_TICKERS = frozenset(f"{c}.JK" for c in LQ45)


# --------------------------------------------------------------------------
# Data loading (cache; invalidate saat data baru masuk)
# --------------------------------------------------------------------------
def _data_version() -> str:
    """Kunci cache murah: jumlah ticker + tanggal terakhir terjauh."""
    storage = Storage()
    ts = storage.tickers(market="IDX")
    last = max((storage.last_date(t) or "" for t in ts), default="")
    return f"{len(ts)}:{last}"


@st.cache_data(show_spinner="Menghitung watchlist, regime & kekuatan relatif...")
def load_core(data_version: str) -> dict:
    storage = Storage()
    tickers = storage.tickers(market="IDX")   # ^JKSE (INDEX) bukan aset tradable
    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None

    # Fitur sekali; likuiditas; skor SEMUA yang lolos (bukan cuma top-N) supaya
    # tab keputusan bisa memberi verdikt per-saham.
    features = {t: compute_features(df) for t, df in ohlcv.items()
                if df is not None and not df.empty}
    liq = screen_liquidity_idx(features, SETTINGS)
    liq_pass = {s.ticker: s.passed for s in liq}
    all_scores = []
    for t, passed in liq_pass.items():
        if not passed:
            continue
        sc = score_ticker(t, features[t], SETTINGS)
        if sc is not None:
            all_scores.append(sc)
    all_scores.sort(key=lambda s: s.composite, reverse=True)

    regime_ok = build_regime(index_df, SETTINGS)
    rs_ok = build_rs(ohlcv, index_df, SETTINGS)
    spec = {t: detect_speculative(ohlcv[t], SETTINGS) for t in tickers}
    return {
        "tickers": tickers, "ohlcv": ohlcv, "index_df": index_df,
        "all_scores": all_scores, "liq": liq, "liq_pass": liq_pass,
        "regime_ok": regime_ok, "rs_ok": rs_ok, "spec": spec,
    }


def _support_resistance(df, lookback: int = 50):
    """Support/resistance kasar: low/high terendah/tertinggi `lookback` bar terakhir."""
    if df is None or df.empty:
        return None, None
    recent = df.tail(lookback)
    return float(recent["low"].min()), float(recent["high"].max())


def _last_bool(m: dict | None) -> bool | None:
    if not m:
        return None
    return bool(m[max(m)])


def _rs_today(rs_ok: dict | None, ticker: str) -> bool | None:
    if not rs_ok or ticker not in rs_ok or not rs_ok[ticker]:
        return None
    d = rs_ok[ticker]
    return bool(d[max(d)])


def _fmt_bool(v: bool | None) -> str:
    return "—" if v is None else ("✔" if v else "✗")  # ✔ lolos / ✗ tidak


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
@st.fragment(run_every=1800)  # cek tiap 30 menit
def _auto_refresh_guard() -> None:
    """
    Pantau kesegaran data di disk. Data harian di-ingest launchd (paper_daily);
    dashboard yang jalan lama TIDAK otomatis rerun. Fragment ini memeriksa
    _data_version() berkala — kalau tanggal terakhir berubah (data baru masuk),
    picu rerun penuh supaya cache (yang berkunci versi) memuat ulang sendiri.
    """
    if _data_version() != st.session_state.get("_data_version"):
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Quant IDX — Dashboard", layout="wide")

    st.session_state["_data_version"] = _data_version()
    _auto_refresh_guard()

    st.title("Quant IDX — Dashboard analisa (read-only)")
    st.caption(SETTINGS.disclaimer + "  •  Dashboard ini TIDAK mengeksekusi "
               "order apa pun; hanya menampilkan sinyal & hasil analisa.")

    storage_has_data = bool(Storage().tickers(market="IDX"))
    if not storage_has_data:
        st.error("Belum ada data. Jalankan dulu: "
                 "`python -m scripts.ingest --index LQ45 --period 10y`")
        st.stop()

    core = load_core(_data_version())
    all_scores = core["all_scores"]
    ohlcv = core["ohlcv"]
    n_universe = len(all_scores)

    # Sidebar controls
    st.sidebar.header("Parameter tampilan")
    capital = st.sidebar.number_input(
        "Modal (Rp)", min_value=1_000_000, value=100_000_000,
        step=1_000_000, format="%d",
        help="Dipakai untuk position sizing di trade plan (bukan eksekusi).")
    top_n = st.sidebar.slider("Tampilkan top-N watchlist", 5,
                              max(n_universe, 5), min(15, max(n_universe, 5)))
    if st.sidebar.button("Muat ulang data (clear cache)"):
        st.cache_data.clear()
        st.rerun()

    # ---- Status header ----
    regime_today = _last_bool(core["regime_ok"])
    last_dt = max((df.index.max() for df in ohlcv.values() if not df.empty),
                  default=None)
    ready = live_readiness(SETTINGS)

    # Kesegaran data: berapa hari BURSA sejak bar terakhir (Sen-Jum, kasar).
    freshness = None
    if last_dt is not None:
        biz = len(pd.bdate_range(last_dt.normalize(),
                                 pd.Timestamp.now().normalize())) - 1
        freshness = ("hari ini" if biz <= 0 else
                     "1 hari bursa lalu" if biz == 1 else
                     f"{biz} hari bursa lalu")

    # Angka IHSG vs EMA supaya alasan regime transparan (bukan sekadar label).
    ihsg_txt = None
    idf = core.get("index_df")
    if idf is not None and not idf.empty:
        close = idf["close"]
        ema = close.ewm(span=SETTINGS.regime.ema_period, adjust=False).mean()
        gap = (close.iloc[-1] / ema.iloc[-1] - 1.0) * 100.0
        ihsg_txt = (f"IHSG {close.iloc[-1]:,.0f} vs EMA{SETTINGS.regime.ema_period} "
                    f"{ema.iloc[-1]:,.0f} ({gap:+.1f}%)")

    c1, c2, c3 = st.columns(3)
    c1.metric("Regime pasar (IHSG)",
              "BULLISH ✔" if regime_today else
              ("BEARISH ✗" if regime_today is not None else "N/A"),
              delta=ihsg_txt, delta_color="off",
              help=f"IHSG vs EMA{SETTINGS.regime.ema_period}. Entry long hanya "
                   "diizinkan saat bullish.")
    c2.metric("Data terakhir",
              last_dt.date().isoformat() if last_dt is not None else "—",
              delta=freshness, delta_color="off",
              help="Bar harian masuk setelah bursa tutup (~17:15 WIB). Dini hari / "
                   "sebelum tutup, tanggal terakhir = penutupan hari bursa sebelumnya.")
    c3.metric("Gerbang LIVE",
              "LOLOS" if ready["allowed"] else "DIBLOKIR",
              help="Live trading tetap dilarang selama ada blocker.")

    if not ready["allowed"]:
        with st.container():
            st.error("**Live trading DIBLOKIR** oleh gerbang kesiapan "
                     "(ini benar & disengaja — jangan bypass):")
            for b in ready["blockers"]:
                st.markdown(f"- {b}")

    tab_wl, tab_plan, tab_bt, tab_detail = st.tabs(
        ["Watchlist", "Trade Plan", "Backtest", "Detail Ticker"])

    # ---- Watchlist ----
    with tab_wl:
        st.subheader(f"Watchlist (top {top_n} skor komposit)")
        rows = []
        for sc in all_scores[:top_n]:
            spec = core["spec"].get(sc.ticker)
            rows.append({
                "Ticker": sc.ticker,
                "Skor": sc.composite,
                "Trend": sc.trend_score,
                "MoneyFlow": sc.moneyflow_score,
                "Klasifikasi": sc.classification,
                "Konfirm kategori": sc.confirming_categories,
                "Volume OK": _fmt_bool(sc.volume_confirmed),
                "RS>IHSG": _fmt_bool(_rs_today(core["rs_ok"], sc.ticker)),
                "Spekulatif": "⚠" if spec else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True)
        if any(core["spec"].get(sc.ticker) for sc in all_scores[:top_n]):
            st.caption("⚠ = terdeteksi karakter tier spekulatif "
                       f"('{SETTINGS.speculative.warning_label}'). "
                       "Perlakukan TERPISAH dari watchlist utama.")

    # ---- Trade Plan ----
    with tab_plan:
        st.subheader("Trade plan — keputusan per saham (swing)")
        if regime_today is False:
            st.warning(
                "**Regime pasar BEARISH — semua entry long DITAHAN (bukan error).**  \n"
                + (f"{ihsg_txt}. " if ihsg_txt else "")
                + f"IHSG di bawah EMA{SETTINGS.regime.ema_period}, jadi gerbang "
                "risk-first memblokir BUY meski ada setup valid (lihat status "
                "**WATCH**). Trade plan akan terisi otomatis begitu IHSG kembali "
                "di atas EMA — ini disengaja, jangan di-bypass.")
        st.caption("**BUY** = lolos gerbang sinyal + regime bullish + RS>IHSG.  "
                   "**WATCH** = setup valid tapi diblokir regime/RS (pantau).  "
                   "**AVOID** = gerbang sinyal belum lolos / tidak likuid.  "
                   "Stop loss WAJIB di tiap rencana. Ini analisa, BUKAN eksekusi.")

        vfilter = st.multiselect("Tampilkan verdikt", ["BUY", "WATCH", "AVOID"],
                                 default=["BUY", "WATCH"])
        st.caption("**Cakupan**: `LQ45 ✓` = universe yang edge-nya sudah "
                   "divalidasi walk-forward.  `extended ⚠` = saham IDX di luar "
                   "LQ45 — sinyal dihitung dengan gerbang yang sama, tapi edge "
                   "OOS-nya BELUM divalidasi di universe ini. Perlakukan "
                   "sebagai kandidat riset, bukan sinyal setara LQ45.")

        decisions = []
        for sc in all_scores:
            spec = bool(core["spec"].get(sc.ticker))
            plan = evaluate_buy(sc, capital, is_speculative=spec,
                                settings=SETTINGS)
            d = decide(sc, plan,
                       regime_ok=regime_today,
                       rs_ok=_rs_today(core["rs_ok"], sc.ticker),
                       liquid=core["liq_pass"].get(sc.ticker, True),
                       speculative=spec)
            decisions.append((sc, d))

        n_buy = sum(1 for _, d in decisions if d.verdict == "BUY")
        n_watch = sum(1 for _, d in decisions if d.verdict == "WATCH")
        n_avoid = sum(1 for _, d in decisions if d.verdict == "AVOID")
        heat = sum(d.risk_amount or 0.0 for _, d in decisions if d.verdict == "BUY")

        h1, h2, h3, h4 = st.columns(4)
        h1.metric("BUY", n_buy)
        h2.metric("WATCH", n_watch)
        h3.metric("AVOID", n_avoid)
        h4.metric("Open risk / 'heat'", f"Rp{heat/1e6:.1f}jt",
                  help="Total risiko jika SEMUA BUY dieksekusi bersamaan "
                       f"(= {heat/capital*100:.1f}% modal). Jaga tetap terkendali.")

        emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}
        plan_rows = []
        for sc, d in decisions:
            if d.verdict not in vfilter:
                continue
            sup, res = _support_resistance(ohlcv.get(sc.ticker))
            validated = sc.ticker in _VALIDATED_TICKERS
            plan_rows.append({
                "Verdikt": f"{emoji.get(d.verdict, '')} {d.verdict}",
                "Ticker": sc.ticker,
                "Cakupan": "LQ45 ✓" if validated else "extended ⚠",
                "Skor": sc.composite,
                "Entry": d.entry if d.entry is not None else "—",
                "Stop": d.stop_loss if d.stop_loss is not None else "—",
                "TP": d.take_profit if d.take_profit is not None else "—",
                "Risiko%": f"{d.downside_pct:.1f}%" if d.downside_pct is not None else "—",
                "Imbalan%": f"{d.upside_pct:.1f}%" if d.upside_pct is not None else "—",
                "RR": f"1:{d.risk_reward}" if d.risk_reward is not None else "—",
                "~Tahan (hari)": d.est_holding_days if d.est_holding_days is not None else "—",
                "Support": round(sup) if sup is not None else "—",
                "Resistance": round(res) if res is not None else "—",
                "Shares": d.shares if d.shares is not None else "—",
                "Risiko (Rp)": round(d.risk_amount) if d.risk_amount is not None else "—",
                "⚠Spek": "⚠" if d.speculative else "",
            })
        if plan_rows:
            st.dataframe(pd.DataFrame(plan_rows), use_container_width=True,
                         hide_index=True)
            st.caption(f"{n_buy} BUY lolos semua gerbang hari ini; {n_watch} "
                       "WATCH (setup ada, konteks belum sejajar). Kolom "
                       "'~Tahan' = perkiraan KASAR (jarak ke TP ÷ ATR), bukan ramalan.")
        else:
            st.info("Tidak ada saham cocok dengan filter verdikt terpilih. "
                    "Coba centang WATCH/AVOID, atau memang belum ada sinyal "
                    "hari ini — normal & sehat, sistem tidak memaksa entry.")

    # ---- Backtest ----
    with tab_bt:
        st.subheader("Backtest terakhir tersimpan")
        latest = load_latest()
        if latest is None:
            st.info("Belum ada backtest tersimpan. Jalankan: "
                    "`python -m scripts.backtest --capital 100000000`")
        else:
            m = latest.get("metrics", {})
            per = latest.get("period", {})
            st.caption(f"Label: `{latest.get('label') or '-'}`  •  "
                       f"Periode: {per.get('start')} s/d {per.get('end')} "
                       f"({per.get('n_days')} hari)  •  "
                       f"dibuat {latest.get('created_utc')}")
            g = st.columns(4)
            g[0].metric("Trade", m.get("n_trades", 0))
            g[1].metric("Win rate", f"{m.get('win_rate', 0)*100:.1f}%")
            g[2].metric("Profit factor", f"{m.get('profit_factor', 0):.2f}")
            g[3].metric("Expectancy", f"{m.get('expectancy_r', 0):+.2f} R")
            g2 = st.columns(4)
            g2[0].metric("Max drawdown", f"{m.get('max_drawdown_pct', 0)*100:.1f}%")
            g2[1].metric("Sharpe", f"{m.get('sharpe', 0):.2f}")
            g2[2].metric("Total return", f"{m.get('total_return_pct', 0)*100:+.1f}%")
            g2[3].metric("CAGR", f"{m.get('cagr_pct', 0)*100:+.1f}%")

            trades = latest.get("trades", [])
            if trades:
                tdf = pd.DataFrame(trades)
                tdf["exit_date"] = pd.to_datetime(tdf["exit_date"])
                tdf = tdf.sort_values("exit_date")
                tdf["PnL kumulatif"] = tdf["pnl"].cumsum()
                st.markdown("**PnL kumulatif terealisasi (per tanggal exit)**")
                st.line_chart(tdf.set_index("exit_date")["PnL kumulatif"])
                st.markdown("**Daftar trade**")
                st.dataframe(tdf.drop(columns=["PnL kumulatif"]),
                             use_container_width=True, hide_index=True)

    # ---- Detail Ticker ----
    with tab_detail:
        st.subheader("Detail ticker")
        sel = st.selectbox("Pilih ticker", core["tickers"])
        df = ohlcv.get(sel)
        if df is None or df.empty:
            st.info("Tidak ada data untuk ticker ini.")
        else:
            n_bars = st.slider("Jumlah bar terakhir", 60, min(len(df), 1000),
                               min(len(df), 250))
            feat = compute_features(df, SETTINGS.indicators).tail(n_bars)
            cols = [c for c in ["close", "ema_50", "ema_200"] if c in feat]
            st.line_chart(feat[cols])
            feat_full = compute_features(df, SETTINGS.indicators)
            sc = score_ticker(sel, feat_full, SETTINGS)
            if sc is not None:
                st.metric("Skor komposit terakhir", sc.composite,
                          help=f"Klasifikasi: {sc.classification}")
                st.markdown("**Alasan skor:**")
                for r in sc.reasons:
                    st.markdown(f"- {r}")

            # ---- Jejak akumulasi/distribusi (proksi 'bandarmology') ----
            st.markdown("---")
            st.markdown("### Jejak akumulasi / distribusi (proksi)")
            fp_lb = st.slider("Window jejak (hari bursa)", 10, 60, 20, key="fp_lb")
            fp = compute_footprint(feat_full, sel, lookback=fp_lb)
            if fp is None:
                st.info("Data belum cukup untuk membaca jejak.")
            else:
                vmap = {"AKUMULASI": "🟢 AKUMULASI",
                        "DISTRIBUSI": "🔴 DISTRIBUSI", "NETRAL": "⚪ NETRAL"}
                f1, f2, f3 = st.columns(3)
                f1.metric("Jejak", vmap.get(fp.verdict, fp.verdict),
                          help="Arah tekanan harga/volume selama window.")
                f2.metric("Keyakinan", fp.confidence,
                          help="Seberapa banyak komponen (A/D, OBV, CMF, volume) "
                               "sepakat arahnya.")
                f3.metric("Δ Harga window", f"{fp.price_change_pct:+.1f}%")
                if fp.absorption:
                    st.warning("⚑ **Absorpsi terdeteksi** — harga flat/turun TAPI "
                               "jejak akumulasi. Klasik 'ada yang mengumpulkan "
                               "diam-diam'. Ini indikasi TERKUAT dari proksi ini.")
                st.markdown("**Rincian jejak:**")
                for s in fp.signals:
                    st.markdown(f"- {s}")
                st.caption("⚠ " + fp.caveat)


if __name__ == "__main__":
    main()
