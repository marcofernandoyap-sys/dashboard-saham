"""
PaperBroker: broker SIMULASI lokal (tanpa uang riil, tanpa network).

Menyimpan kas & posisi ke file JSON kecil (data/paper_account.json) supaya
rekam jejak paper-trading terakumulasi lintas run — inilah yang memberi makan
gerbang kesiapan live (butuh min hari & min trade paper).

Model isi order (konservatif, jujur):
  - Order diisi di `ref_price` (mis. close hari itu) — tanpa asumsi harga lebih
    baik. Slippage tak dimodelkan optimistis.
  - Fee dikenakan tiap sisi (default 20 bps, seperti backtest).
  - BUY ditolak kalau kas tak cukup; SELL ditolak kalau posisi tak cukup.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quant.config import DATA_DIR, SETTINGS
from quant.execution.broker import Account, Broker, Fill, Order, Position

PAPER_STATE_PATH = DATA_DIR / "paper_account.json"


class PaperBroker(Broker):
    name = "paper"
    is_live = False                 # simulasi -> tak pernah uang riil

    def __init__(self, initial_cash: float = 100_000_000.0,
                 fee_bps: float | None = None,
                 state_path: Path | str = PAPER_STATE_PATH):
        self.fee_bps = (fee_bps if fee_bps is not None
                        else SETTINGS.execution.default_fee_bps)
        self.state_path = Path(state_path)
        self._state = self._load(initial_cash)

    # ------------------------------------------------------------- state I/O
    def _load(self, initial_cash: float) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"cash": float(initial_cash), "positions": {}}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2))

    # --------------------------------------------------------------- helpers
    def _fee(self, notional: float) -> float:
        return abs(notional) * self.fee_bps / 10_000.0

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _reject(self, order: Order, reason: str) -> Fill:
        return Fill(ticker=order.ticker, side=order.side, qty=order.qty,
                    price=0.0, fee=0.0, ts=self._now(), status="rejected",
                    broker=self.name, reason=reason)

    # ---------------------------------------------------------------- submit
    def submit(self, order: Order) -> Fill:
        price = order.limit_price if order.order_type == "limit" else order.ref_price
        if price is None or price <= 0:
            return self._reject(order, "harga acuan tidak valid")
        if order.qty <= 0:
            return self._reject(order, "qty <= 0")

        notional = price * order.qty
        fee = self._fee(notional)
        positions: dict = self._state["positions"]
        side = order.side.lower()

        if side == "buy":
            cost = notional + fee
            if cost > self._state["cash"] + 1e-6:
                return self._reject(
                    order, f"kas tak cukup (butuh {cost:,.0f}, "
                           f"ada {self._state['cash']:,.0f})")
            self._state["cash"] -= cost
            pos = positions.get(order.ticker)
            if pos:
                total_qty = pos["qty"] + order.qty
                pos["avg_price"] = ((pos["avg_price"] * pos["qty"]
                                     + price * order.qty) / total_qty)
                pos["qty"] = total_qty
            else:
                positions[order.ticker] = {"qty": order.qty, "avg_price": price}
        elif side == "sell":
            pos = positions.get(order.ticker)
            if not pos or pos["qty"] < order.qty:
                held = pos["qty"] if pos else 0
                return self._reject(
                    order, f"posisi tak cukup (jual {order.qty}, punya {held})")
            self._state["cash"] += notional - fee
            pos["qty"] -= order.qty
            if pos["qty"] == 0:
                positions.pop(order.ticker)
        else:
            return self._reject(order, f"side tak dikenal: {order.side!r}")

        self._save()
        return Fill(ticker=order.ticker, side=side, qty=order.qty, price=price,
                    fee=fee, ts=self._now(), status="filled", broker=self.name,
                    order_id=f"paper-{order.ticker}-{self._now()}")

    # -------------------------------------------------------------- queries
    def positions(self) -> list[Position]:
        return [Position(t, p["qty"], p["avg_price"])
                for t, p in self._state["positions"].items()]

    def account(self, marks: dict[str, float] | None = None) -> Account:
        """
        Ekuitas = kas + nilai posisi. `marks` = {ticker: harga terkini}; kalau
        tak diberi, pakai avg_price (konservatif, bukan mark-to-market).
        """
        marks = marks or {}
        pos = self.positions()
        mv = sum(p.qty * marks.get(p.ticker, p.avg_price) for p in pos)
        return Account(cash=self._state["cash"],
                       equity=self._state["cash"] + mv, positions=pos)
