"""
State dedup notifikasi: simpan sidik jari digest terakhir yang BERHASIL dikirim,
supaya cron yang jalan berkali-kali tidak mengirim pesan identik berulang.

Sengaja file JSON kecil (bukan tabel DB) agar mandiri & mudah dihapus/diinspeksi.
Path default di-gitignore (data/notify_state.json).
"""
from __future__ import annotations

import json
from pathlib import Path

from quant.config import DATA_DIR

STATE_PATH = DATA_DIR / "notify_state.json"


def last_signature(path: Path | str = STATE_PATH) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("last_signature")
    except (json.JSONDecodeError, OSError):
        return None


def record_sent(signature: str, date: str, channel: str,
                path: Path | str = STATE_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "last_signature": signature,
        "last_date": date,
        "last_channel": channel,
    }, indent=2))


def already_sent(signature: str, path: Path | str = STATE_PATH) -> bool:
    return last_signature(path) == signature
