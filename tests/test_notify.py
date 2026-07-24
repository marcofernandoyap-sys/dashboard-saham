#!/usr/bin/env python3
"""
Uji digest & dedup notifikasi — TANPA network (console/telegram/email tak dipanggil).

Jalankan: python -m tests.test_notify
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant.config import SETTINGS
from quant.notify.channels import (ChannelConfigError, ConsoleNotifier,
                                   get_notifier)
from quant.notify.digest import (build_daily_report, format_digest,
                                 report_signature)
from quant.notify.state import already_sent, last_signature, record_sent


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        raise AssertionError(msg)


def make_trending_market(n_tickers: int = 6, n: int = 400, seed: int = 11) -> dict:
    """Beberapa saham tren naik (agar ada sinyal BUY) — sama pola dgn test_backtest."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    out = {}
    for k in range(n_tickers):
        drift = 0.0008 + 0.0004 * k / n_tickers
        rets = rng.normal(drift, 0.012, n)
        close = 1000 * np.cumprod(1 + rets)
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


def make_index(n: int = 400, seed: int = 3) -> pd.DataFrame:
    """IHSG sintetis datar-naik pelan (regime kadang bullish)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    close = 7000 * np.cumprod(1 + rng.normal(0.0003, 0.008, n))
    return pd.DataFrame({
        "open": close, "high": close * 1.003, "low": close * 0.997,
        "close": close, "volume": np.full(n, 1e9),
    }, index=dates)


def test_report_and_format() -> None:
    print("\n== Digest build & format ==")
    market = make_trending_market()
    index_df = make_index()
    report = build_daily_report(market, index_df, capital=100_000_000,
                                settings=SETTINGS)
    check(report.date != "N/A", "report punya tanggal valid")
    check(isinstance(report.actionable, list), "actionable adalah list")
    check(isinstance(report.blocked_signals, list), "blocked_signals adalah list")
    # actionable ∩ blocked kosong (alert tak boleh di dua tempat)
    a = {x.ticker for x in report.actionable}
    b = {x.ticker for x in report.blocked_signals}
    check(a.isdisjoint(b), "actionable & blocked tidak beririsan")
    text = format_digest(report, SETTINGS)
    check(report.date in text, "tanggal muncul di teks digest")
    check(SETTINGS.disclaimer in text, "disclaimer muncul di teks digest")
    check("bukan eksekusi" in text.lower(), "penegasan 'alert bukan eksekusi' ada")


def test_signature_stability() -> None:
    print("\n== Signature stabil & sensitif isi ==")
    market = make_trending_market()
    index_df = make_index()
    r1 = build_daily_report(market, index_df, 100_000_000, SETTINGS)
    r2 = build_daily_report(market, index_df, 100_000_000, SETTINGS)
    check(report_signature(r1) == report_signature(r2),
          "report identik -> signature identik")
    # ubah tanggal -> signature berubah
    r3 = replace(r2, date="1999-01-01")
    check(report_signature(r3) != report_signature(r1),
          "tanggal beda -> signature beda")


def test_dedup_state_roundtrip() -> None:
    print("\n== Dedup state (file JSON sementara) ==")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "notify_state.json"
        sig = "abc123def456"
        check(last_signature(path) is None, "state kosong -> None")
        check(not already_sent(sig, path), "belum pernah kirim -> False")
        record_sent(sig, "2026-07-20", "telegram", path)
        check(last_signature(path) == sig, "signature tersimpan terbaca kembali")
        check(already_sent(sig, path), "signature sama -> sudah terkirim True")
        check(not already_sent("lainnya", path), "signature lain -> False")


def test_console_channel_never_fails() -> None:
    print("\n== Channel console (dry-run) ==")
    n = get_notifier("console")
    check(isinstance(n, ConsoleNotifier), "channel 'console' -> ConsoleNotifier")
    check(n.send("subjek uji", "isi pesan uji") is True,
          "console.send selalu True (dry-run)")


def test_unknown_channel_raises() -> None:
    print("\n== Channel tak dikenal ditolak ==")
    raised = False
    try:
        get_notifier("merpati-pos")
    except ChannelConfigError:
        raised = True
    check(raised, "channel asing -> ChannelConfigError")


def test_missing_credentials_raises() -> None:
    print("\n== Kredensial telegram wajib ==")
    import os
    saved = {k: os.environ.pop(k, None)
             for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    try:
        raised = False
        try:
            get_notifier("telegram")
        except ChannelConfigError:
            raised = True
        check(raised, "telegram tanpa kredensial -> ChannelConfigError")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    print("Menjalankan uji notifikasi...")
    test_report_and_format()
    test_signature_stability()
    test_dedup_state_roundtrip()
    test_console_channel_never_fails()
    test_unknown_channel_raises()
    test_missing_credentials_raises()
    print("\nSemua uji notifikasi LULUS.\n")
