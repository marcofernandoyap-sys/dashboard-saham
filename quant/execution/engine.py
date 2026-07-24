"""
ExecutionEngine: satu-satunya jalur order. Menegakkan GERBANG RISIKO sebelum
meneruskan ke broker mana pun.

Alur:
  TradePlan (dari signals.evaluate_buy) -> bulatkan lot (IDX=100) -> cek gerbang
  -> broker.submit() -> catat ke Journal.

GERBANG untuk order LIVE (broker.is_live True ATAU mode='live'):
  1. execution.allow_live harus True (sakelar sadar).
  2. registry.live_readiness harus 'allowed' (backtest: PF>1, exp>0, ret>0, Sharpe>=1).
  3. journal.paper_readiness harus 'allowed' (min hari & min trade paper).
Kalau salah satu gagal -> order DITOLAK (tidak diteruskan ke broker).

Order PAPER tidak pernah di-gate oleh #2/#3 — justru dari situ rekam jejak
paper dikumpulkan. Ini SENGAJA: paper adalah cara jujur menuju kelayakan live.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from quant.analysis.signals import TradePlan
from quant.backtest.registry import live_readiness
from quant.config import SETTINGS
from quant.execution.broker import Broker, Fill, Order
from quant.execution.journal import Journal, paper_readiness


class ExecutionBlocked(RuntimeError):
    """Order live ditolak karena gerbang risiko belum lolos."""


@dataclass
class ExecutionDecision:
    submitted: bool
    fill: Fill | None
    blockers: list[str] = field(default_factory=list)
    note: str = ""


def _round_lot(shares: int, lot: int) -> int:
    return (shares // lot) * lot if lot > 1 else shares


class ExecutionEngine:
    def __init__(self, broker: Broker, settings=SETTINGS,
                 journal: Journal | None = None, is_idx: bool = True):
        self.broker = broker
        self.settings = settings
        self.journal = journal or Journal()
        self.is_idx = is_idx

    @property
    def mode(self) -> str:
        # Live kalau broker menyentuh uang riil ATAU config eksplisit minta live.
        return "live" if (self.broker.is_live
                          or self.settings.execution.mode == "live") else "paper"

    # ------------------------------------------------------------- gerbang
    def live_blockers(self) -> list[str]:
        """Kumpulkan SEMUA alasan order live belum boleh (kosong = boleh)."""
        ex = self.settings.execution
        blockers: list[str] = []
        if not ex.allow_live:
            blockers.append("execution.allow_live=False (sakelar uang-riil MATI)")
        if ex.require_backtest_gate:
            ready = live_readiness(self.settings)
            if not ready["allowed"]:
                blockers += [f"backtest: {b}" for b in ready["blockers"]]
        if ex.require_paper_track_record:
            pr = paper_readiness(self.journal, self.settings)
            if not pr["allowed"]:
                blockers += [f"paper: {b}" for b in pr["blockers"]]
        return blockers

    # ------------------------------------------------------------- eksekusi
    def submit_order(self, order: Order) -> ExecutionDecision:
        # Bulatkan lot IDX ke bawah (risiko tak boleh melar di atas batas).
        if self.is_idx and order.side.lower() == "buy":
            lot = self.settings.execution.lot_size_idx
            rounded = _round_lot(order.qty, lot)
            if rounded <= 0:
                return ExecutionDecision(
                    False, None,
                    [f"qty {order.qty} < 1 lot ({lot}) -> tidak dieksekusi"])
            order.qty = rounded

        # GERBANG: hanya untuk jalur live (uang riil).
        if self.mode == "live":
            blockers = self.live_blockers()
            if blockers:
                return ExecutionDecision(
                    False, None, blockers,
                    note="Order LIVE DITOLAK oleh gerbang risiko (tidak di-bypass).")

        fill = self.broker.submit(order)
        self.journal.record(fill, mode=self.mode)
        note = ("terisi" if fill.ok else f"ditolak broker: {fill.reason}")
        return ExecutionDecision(fill.ok, fill, [], note=note)

    def execute_plan(self, plan: TradePlan) -> ExecutionDecision:
        """Ubah TradePlan BUY jadi order & submit. NO_TRADE -> tidak apa-apa."""
        if plan.action != "BUY":
            return ExecutionDecision(False, None,
                                     [f"plan.action={plan.action} (bukan BUY)"])
        order = Order(
            ticker=plan.ticker, side="buy", qty=plan.shares,
            order_type="market", ref_price=plan.entry,
            stop_loss=plan.stop_loss, take_profit=plan.take_profit,
            client_id=f"{plan.ticker}-{plan.date}",
        )
        return self.submit_order(order)
