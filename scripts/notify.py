#!/usr/bin/env python3
"""
CLI: bangun digest harian IDX & kirim sebagai ALERT (bukan eksekusi order).

Contoh:
    python -m scripts.notify                       # DRY-RUN ke console
    python -m scripts.notify --channel telegram    # butuh kredensial di .env
    python -m scripts.notify --force               # abaikan dedup, kirim ulang

Default AMAN:
  - channel 'console' -> hanya cetak ke stdout (tak pernah gagal, tak kirim apa-apa).
  - dedup aktif        -> digest identik tidak dikirim dua kali.
  - kalau tidak ada sinyal, TIDAK mengirim (kecuali notify.send_when_empty True).

TIDAK ADA eksekusi order di sini. Ini murni pemberitahuan/analisa.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant.config import SETTINGS
from quant.data.storage import Storage
from quant.notify.channels import ChannelConfigError, get_notifier
from quant.notify.digest import (build_daily_report, format_digest,
                                 report_signature)
from quant.notify.state import already_sent, record_sent


def main() -> int:
    ap = argparse.ArgumentParser(description="Digest & alert harian IDX (read-only)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--channel", default=None,
                    help="console | telegram | email (default: dari config)")
    ap.add_argument("--force", action="store_true",
                    help="abaikan dedup (kirim ulang walau isi identik)")
    args = ap.parse_args()

    channel = (args.channel or SETTINGS.notify.channel).lower()

    storage = Storage()
    tickers = storage.tickers(market="IDX")     # indeks BUKAN aset tradable
    if not tickers:
        print("Belum ada data. Jalankan dulu: python -m scripts.ingest --index LQ45")
        return 1

    ohlcv = {t: storage.load_ohlcv(t) for t in tickers}
    index_df = storage.load_ohlcv(SETTINGS.regime.index_ticker)
    index_df = index_df if not index_df.empty else None

    report = build_daily_report(ohlcv, index_df, args.capital, SETTINGS)
    body = format_digest(report, SETTINGS)
    subject = f"QUANT IDX — {report.date} — {len(report.actionable)} sinyal BUY"

    # Tidak ada sinyal & tidak diminta kirim kosong -> berhenti (tetap tampilkan preview).
    if not report.actionable and not SETTINGS.notify.send_when_empty:
        print(body)
        print("\n[notify] Tidak ada sinyal actionable & send_when_empty=False -> "
              "tidak mengirim.")
        return 0

    signature = report_signature(report)
    if (SETTINGS.notify.dedup_enabled and not args.force
            and already_sent(signature)):
        print(body)
        print(f"\n[notify] Digest identik sudah pernah dikirim (sig={signature}). "
              "Pakai --force untuk kirim ulang.")
        return 0

    try:
        notifier = get_notifier(channel)
    except ChannelConfigError as e:
        print(f"[notify] konfigurasi channel gagal: {e}")
        return 2

    ok = notifier.send(subject, body)
    if not ok:
        print(f"[notify] pengiriman via '{channel}' GAGAL (lihat pesan di atas).")
        return 3

    # Console adalah dry-run: jangan catat sebagai 'terkirim' supaya tak menekan alert nyata.
    if channel != "console":
        record_sent(signature, report.date, channel)
        print(f"\n[notify] Terkirim via '{channel}' (sig={signature}) & dicatat.")
    else:
        print(f"\n[notify] DRY-RUN console selesai (sig={signature}, tidak dicatat).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
