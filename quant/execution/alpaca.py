"""
AlpacaBroker: eksekusi saham US via Alpaca (stdlib urllib, tanpa SDK).

DEFAULT paper (uang virtual) -> paper-api.alpaca.markets. is_live=False.
Mode live (uang riil) -> api.alpaca.markets, is_live=True; engine akan
menegakkan gerbang risiko sebelum submit.

Alpaca TIDAK mendukung saham IDX — broker ini khusus universe US (Fase 5b).
Kredensial dibaca dari environment (.env), tak pernah di-commit:
  ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from quant.execution.broker import (Account, Broker, BrokerError, Fill, Order,
                                    Position)

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, key_id: str, secret_key: str, paper: bool = True):
        self.key_id = key_id
        self.secret_key = secret_key
        self.paper = paper
        self.is_live = not paper           # live=uang riil -> di-gate engine
        self.base = PAPER_BASE if paper else LIVE_BASE

    # --------------------------------------------------------------- HTTP
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("APCA-API-KEY-ID", self.key_id)
        req.add_header("APCA-API-SECRET-KEY", self.secret_key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise BrokerError(f"Alpaca HTTP {e.code}: {detail}") from e
        except Exception as e:                      # noqa: BLE001
            raise BrokerError(f"Alpaca error: {e}") from e

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -------------------------------------------------------------- submit
    def submit(self, order: Order) -> Fill:
        payload = {
            "symbol": order.ticker,
            "qty": str(order.qty),
            "side": order.side.lower(),
            "type": "market" if order.order_type != "limit" else "limit",
            "time_in_force": "day",
        }
        if order.order_type == "limit" and order.limit_price:
            payload["limit_price"] = str(order.limit_price)
        try:
            resp = self._request("POST", "/v2/orders", payload)
        except BrokerError as e:
            return Fill(ticker=order.ticker, side=order.side, qty=order.qty,
                        price=0.0, fee=0.0, ts=self._now(), status="rejected",
                        broker=self.name, reason=str(e))
        filled_price = resp.get("filled_avg_price") or resp.get("limit_price") or 0.0
        return Fill(
            ticker=order.ticker, side=order.side.lower(), qty=order.qty,
            price=float(filled_price or 0.0), fee=0.0, ts=self._now(),
            status="filled", broker=self.name, order_id=str(resp.get("id", "")),
            reason=str(resp.get("status", "")),
        )

    # -------------------------------------------------------------- queries
    def positions(self) -> list[Position]:
        data = self._request("GET", "/v2/positions")
        rows = data if isinstance(data, list) else []
        return [Position(r["symbol"], int(float(r["qty"])),
                         float(r["avg_entry_price"])) for r in rows]

    def account(self) -> Account:
        a = self._request("GET", "/v2/account")
        return Account(cash=float(a.get("cash", 0.0)),
                       equity=float(a.get("equity", 0.0)),
                       positions=self.positions())


def from_env(paper: bool = True) -> AlpacaBroker:
    """Bangun AlpacaBroker dari kredensial environment (.env)."""
    key = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise BrokerError(
            "Kredensial Alpaca kurang: set ALPACA_API_KEY_ID & "
            "ALPACA_API_SECRET_KEY di .env (lihat .env.example).")
    return AlpacaBroker(key, secret, paper=paper)
