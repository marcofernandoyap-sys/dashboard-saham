"""
Bangun 'digest' harian dari analysis engine + gerbang regime/RS.

Pemisahan tegas:
  - build_daily_report(): hitung fakta (murni; bisa diuji tanpa network).
  - format_digest():      ubah report -> teks siap kirim.
  - report_signature():   sidik jari untuk dedup (jangan kirim yang identik 2x).

Semua keputusan "actionable" mengikuti aturan yang SAMA dengan backtest & CLI:
BUY final butuh lolos gerbang sinyal DAN regime pasar bullish DAN kekuatan
relatif > IHSG. Tidak ada logika sinyal baru di sini.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd

from quant.analysis.screener import build_watchlist, detect_speculative
from quant.analysis.signals import evaluate_buy
from quant.backtest.engine import build_regime, build_rs
from quant.backtest.registry import live_readiness
from quant.config import SETTINGS


@dataclass
class Alert:
    ticker: str
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    shares: int
    risk_amount: float
    speculative: bool
    blocked_by: list[str] = field(default_factory=list)   # kosong = actionable


@dataclass
class DailyReport:
    date: str
    regime_bullish: bool | None
    live_allowed: bool
    live_blockers: list[str]
    actionable: list[Alert]                # lolos SEMUA gerbang
    blocked_signals: list[Alert]           # lolos sinyal, diblokir regime/RS
    top_watchlist: list[dict]              # ringkasan skor teratas
    disclaimer: str


def _last_bool(m: dict | None) -> bool | None:
    return None if not m else bool(m[max(m)])


def _rs_today(rs_ok: dict | None, ticker: str) -> bool | None:
    if not rs_ok or ticker not in rs_ok or not rs_ok[ticker]:
        return None
    d = rs_ok[ticker]
    return bool(d[max(d)])


def build_daily_report(ohlcv_by_ticker: dict[str, pd.DataFrame],
                       index_df: pd.DataFrame | None,
                       capital: float,
                       settings=SETTINGS) -> DailyReport:
    watchlist, _ = build_watchlist(ohlcv_by_ticker, settings)
    regime_ok = build_regime(index_df, settings)
    rs_ok = build_rs(ohlcv_by_ticker, index_df, settings)
    regime_today = _last_bool(regime_ok)

    last_dt = max((df.index.max() for df in ohlcv_by_ticker.values()
                   if df is not None and not df.empty), default=None)
    date_str = last_dt.date().isoformat() if last_dt is not None else "N/A"

    actionable: list[Alert] = []
    blocked: list[Alert] = []
    for sc in watchlist:
        df = ohlcv_by_ticker.get(sc.ticker)
        spec = detect_speculative(df, settings) if df is not None else None
        plan = evaluate_buy(sc, capital, is_speculative=bool(spec),
                            settings=settings)
        if plan.action != "BUY":
            continue
        rs_today = _rs_today(rs_ok, sc.ticker)
        reasons: list[str] = []
        # Gerbang regime hanya menghalangi kalau filter aktif & peta tersedia.
        if settings.regime.enabled and regime_today is False:
            reasons.append("regime pasar bearish (IHSG < EMA200)")
        if settings.rs.enabled and rs_today is False:
            reasons.append("kekuatan relatif < IHSG")
        alert = Alert(
            ticker=plan.ticker, entry=plan.entry, stop_loss=plan.stop_loss,
            take_profit=plan.take_profit, risk_reward=plan.risk_reward,
            shares=plan.shares, risk_amount=plan.risk_amount,
            speculative=bool(spec), blocked_by=reasons,
        )
        (actionable if not reasons else blocked).append(alert)

    top = [{"ticker": sc.ticker, "composite": sc.composite,
            "classification": sc.classification}
           for sc in watchlist[:settings.notify.top_watchlist_in_digest]]

    ready = live_readiness(settings)
    return DailyReport(
        date=date_str, regime_bullish=regime_today,
        live_allowed=ready["allowed"], live_blockers=ready["blockers"],
        actionable=actionable, blocked_signals=blocked, top_watchlist=top,
        disclaimer=settings.disclaimer,
    )


def _fmt_alert(a: Alert) -> str:
    tag = " [SPEKULATIF]" if a.speculative else ""
    head = (f"• {a.ticker}{tag}  entry {a.entry:,.0f}  SL {a.stop_loss:,.0f}  "
            f"TP {a.take_profit:,.0f}  RR 1:{a.risk_reward}  "
            f"{a.shares} shares  risiko Rp{a.risk_amount:,.0f}")
    if a.blocked_by:
        head += "  (DIBLOKIR: " + "; ".join(a.blocked_by) + ")"
    return head


def format_digest(report: DailyReport, settings=SETTINGS) -> str:
    lines: list[str] = []
    regime = ("BULLISH" if report.regime_bullish else
              ("BEARISH" if report.regime_bullish is not None else "N/A"))
    lines.append(f"QUANT IDX — {report.date}")
    lines.append(f"Regime pasar (IHSG): {regime}")
    lines.append("Gerbang LIVE: " +
                 ("LOLOS" if report.live_allowed else "DIBLOKIR"))
    lines.append("")

    if report.actionable:
        lines.append(f"SINYAL BUY ({len(report.actionable)}) — lolos semua gerbang:")
        lines.extend(_fmt_alert(a) for a in report.actionable)
    else:
        lines.append("Tidak ada sinyal BUY yang lolos semua gerbang hari ini.")
        lines.append("(Normal & sehat — sistem tidak memaksa entry.)")

    if settings.notify.include_blocked_signals and report.blocked_signals:
        lines.append("")
        lines.append(f"Sinyal terblokir gerbang ({len(report.blocked_signals)}):")
        lines.extend(_fmt_alert(a) for a in report.blocked_signals)

    if report.top_watchlist:
        lines.append("")
        lines.append("Watchlist teratas:")
        for w in report.top_watchlist:
            lines.append(f"  {w['ticker']}: skor {w['composite']} "
                         f"({w['classification']})")

    lines.append("")
    lines.append("— " + report.disclaimer)
    lines.append("Catatan: ini ALERT, bukan eksekusi. Keputuskan & order manual.")
    return "\n".join(lines)


def report_signature(report: DailyReport) -> str:
    """
    Sidik jari untuk dedup: tanggal + regime + daftar (ticker actionable + entry).
    Kalau isi actionable sama untuk tanggal yang sama -> dianggap identik.
    """
    payload = [report.date, str(report.regime_bullish)]
    for a in sorted(report.actionable, key=lambda x: x.ticker):
        payload.append(f"{a.ticker}:{a.entry}:{a.stop_loss}")
    raw = "|".join(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
