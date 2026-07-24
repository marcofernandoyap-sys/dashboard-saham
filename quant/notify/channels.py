"""
Channel pengiriman notifikasi — stdlib saja (tanpa requests/dependency baru):
  - ConsoleNotifier : cetak ke stdout (DEFAULT & dry-run; tak pernah gagal).
  - TelegramNotifier : Bot API via urllib (butuh token & chat_id).
  - EmailNotifier    : SMTP via smtplib (butuh host/port/kredensial/from/to).

Kredensial dibaca dari environment (opsional dari file .env yang TIDAK
ter-commit). Tidak ada rahasia yang disimpan di config.py atau kode.
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from email.mime.text import MIMEText
from pathlib import Path

from quant.config import PROJECT_ROOT


def load_dotenv(path: Path | str | None = None) -> None:
    """
    Muat KEY=VALUE dari .env ke os.environ (hanya kalau belum diset).
    Parser minimal: abaikan baris kosong & komentar (#). Tanpa dependency.
    """
    p = Path(path) if path else PROJECT_ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


class Notifier(ABC):
    name = "base"

    @abstractmethod
    def send(self, subject: str, body: str) -> bool:
        """Kirim pesan. Return True kalau sukses. Tidak melempar ke pemanggil."""
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, subject: str, body: str) -> bool:
        print("=" * 64)
        print(f"[DRY-RUN / CONSOLE] {subject}")
        print("=" * 64)
        print(body)
        print("=" * 64)
        return True


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, subject: str, body: str) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": f"*{subject}*\n\n{body}",
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                print(f"[telegram] gagal: {payload}")
                return False
            return True
        except Exception as e:                      # noqa: BLE001 - laporkan saja
            print(f"[telegram] error: {e}")
            return False


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str,
                 sender: str, recipient: str, use_tls: bool = True):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.sender, self.recipient = sender, recipient
        self.use_tls = use_tls

    def send(self, subject: str, body: str) -> bool:
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.recipient
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as s:
                if self.use_tls:
                    s.starttls()
                if self.user:
                    s.login(self.user, self.password)
                s.sendmail(self.sender, [self.recipient], msg.as_string())
            return True
        except Exception as e:                      # noqa: BLE001
            print(f"[email] error: {e}")
            return False


class ChannelConfigError(RuntimeError):
    """Kredensial channel tidak lengkap di environment."""


def _require(*keys: str) -> list[str]:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise ChannelConfigError(
            "Environment kurang: " + ", ".join(missing) +
            " (set di .env atau shell). Lihat .env.example.")
    return [os.environ[k] for k in keys]


def get_notifier(channel: str) -> Notifier:
    """
    Bangun notifier sesuai channel. Kredensial dari environment.
    'console' selalu tersedia (dry-run). Untuk telegram/email, kredensial WAJIB.
    """
    load_dotenv()
    channel = (channel or "console").lower()
    if channel == "console":
        return ConsoleNotifier()
    if channel == "telegram":
        token, chat = _require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        return TelegramNotifier(token, chat)
    if channel == "email":
        host, port, sender, to = _require(
            "SMTP_HOST", "SMTP_PORT", "EMAIL_FROM", "EMAIL_TO")
        user = os.environ.get("SMTP_USER", "")
        pw = os.environ.get("SMTP_PASSWORD", "")
        return EmailNotifier(host, int(port), user, pw, sender, to)
    raise ChannelConfigError(f"channel tidak dikenal: {channel!r} "
                             "(pilih: console | telegram | email)")
