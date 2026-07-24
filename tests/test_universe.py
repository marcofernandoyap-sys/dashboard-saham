#!/usr/bin/env python3
"""
Uji parser universe TradingView (full IDX) — TANPA network.

Memvalidasi:
  - Normalisasi simbol: strip 'IDX:' prefix, uppercase, tambah/rapikan '.JK'.
  - Parser CSV: deteksi kolom simbol, dedup jaga urutan, buang baris kosong.
  - idx_tickers('IDX') memuat dari CSV; LQ45/IDX30 tetap dari snapshot.

Jalankan: python -m tests.test_universe
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.universe import (_normalize_idx_symbol, idx_tickers,
                            load_tradingview_csv)


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        raise AssertionError(msg)


def test_normalize() -> None:
    print("\n== Normalisasi simbol ==")
    check(_normalize_idx_symbol("IDX:BBCA") == "BBCA.JK", "strip prefix IDX:")
    check(_normalize_idx_symbol("bbca") == "BBCA.JK", "uppercase + .JK")
    check(_normalize_idx_symbol("BBCA.JK") == "BBCA.JK", ".JK idempoten")
    check(_normalize_idx_symbol(" TLKM ") == "TLKM.JK", "trim spasi")
    check(_normalize_idx_symbol("") is None, "kosong -> None")
    check(_normalize_idx_symbol("  ") is None, "spasi -> None")
    check(_normalize_idx_symbol("FOO BAR") is None, "non-alnum -> None")


def test_load_csv() -> None:
    print("\n== Parser CSV TradingView ==")
    content = (
        "Ticker,Description\n"
        "IDX:BBCA,Bank Central Asia\n"
        "IDX:TLKM,Telkom\n"
        "IDX:BBCA,Duplikat\n"        # duplikat -> dibuang
        "bbri,Bank Rakyat\n"        # lowercase
        ",Baris kosong\n"           # simbol kosong -> dilewati
        "BMRI.JK,Sudah bersuffix\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tv.csv"
        p.write_text(content, encoding="utf-8")
        tickers = load_tradingview_csv(p)
    check(tickers == ["BBCA.JK", "TLKM.JK", "BBRI.JK", "BMRI.JK"],
          f"parse+dedup+normalize benar ({tickers})")

    # Fallback kolom pertama saat tak ada header 'Ticker'/'Symbol'.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tv2.csv"
        p.write_text("kode,harga\nBBCA,9000\nTLKM,3000\n", encoding="utf-8")
        tickers2 = load_tradingview_csv(p)
    check(tickers2 == ["BBCA.JK", "TLKM.JK"], f"fallback kolom pertama ({tickers2})")


def test_idx_tickers_dispatch() -> None:
    print("\n== idx_tickers dispatch ==")
    lq = idx_tickers("LQ45")
    check(all(t.endswith(".JK") for t in lq) and len(lq) > 30,
          f"LQ45 snapshot (.JK, {len(lq)} nama)")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tv.csv"
        p.write_text("Symbol\nIDX:GOTO\nIDX:BUKA\n", encoding="utf-8")
        full = idx_tickers("IDX", csv_path=p)
    check(full == ["GOTO.JK", "BUKA.JK"], f"index=IDX baca CSV ({full})")

    try:
        idx_tickers("TIDAKADA")
        check(False, "index tak dikenal harus ValueError")
    except ValueError:
        check(True, "index tak dikenal -> ValueError")


if __name__ == "__main__":
    print("Menjalankan uji universe TradingView...")
    test_normalize()
    test_load_csv()
    test_idx_tickers_dispatch()
    print("\nSemua uji LULUS. Parser universe berfungsi.\n")
