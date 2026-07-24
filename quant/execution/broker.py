"""
Kontrak broker abstrak + tipe data order/fill/posisi/akun.

Semua broker (paper lokal, Alpaca US, broker IDX riil nanti) mengimplementasikan
interface `Broker` yang SAMA sehingga ExecutionEngine tak perlu tahu detail broker.

Konvensi:
  - side: "buy" | "sell"
  - qty : jumlah saham (integer). Pembulatan lot (IDX=100) dilakukan di engine.
  - is_live: True HANYA untuk broker yang menyentuh uang riil. Dipakai engine
    untuk memutuskan apakah gerbang risiko wajib lolos sebelum submit.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Order:
    ticker: str
    side: str                       # "buy" | "sell"
    qty: int
    order_type: str = "market"      # "market" | "limit"
    limit_price: float | None = None
    ref_price: float | None = None  # harga acuan (mis. close) utk isi paper
    stop_loss: float | None = None  # dibawa utk jurnal/manajemen risiko
    take_profit: float | None = None
    client_id: str | None = None


@dataclass
class Fill:
    ticker: str
    side: str
    qty: int
    price: float
    fee: float
    ts: str
    status: str                     # "filled" | "rejected"
    broker: str = "base"
    order_id: str = ""
    reason: str = ""                # alasan kalau rejected

    @property
    def ok(self) -> bool:
        return self.status == "filled"


@dataclass
class Position:
    ticker: str
    qty: int
    avg_price: float


@dataclass
class Account:
    cash: float
    equity: float
    positions: list[Position] = field(default_factory=list)


class BrokerError(RuntimeError):
    """Kegagalan level broker (kredensial, jaringan, penolakan API)."""


class Broker(ABC):
    name: str = "base"
    is_live: bool = False           # True = uang riil (di-gate ketat oleh engine)

    @abstractmethod
    def submit(self, order: Order) -> Fill:
        """Kirim order. Return Fill (status 'filled'/'rejected'). Tidak melempar
        untuk penolakan normal; hanya BrokerError untuk kegagalan infrastruktur."""
        raise NotImplementedError

    @abstractmethod
    def account(self) -> Account:
        raise NotImplementedError

    @abstractmethod
    def positions(self) -> list[Position]:
        raise NotImplementedError


class LiveBrokerNotConfigured(Broker):
    """
    Placeholder untuk broker IDX LIVE (uang riil) yang BELUM tersedia.

    Sengaja MENOLAK semua operasi: tidak ada API broker IDX riil di repo ini,
    dan mengaktifkan eksekusi uang-riil saat gerbang kesiapan masih DIBLOKIR
    melanggar prinsip #1 proyek (risk management). Interface ini ada agar broker
    riil bisa "dicolok" nanti tanpa mengubah engine — TAPI hanya setelah:
      1) backtest lolos gerbang (Sharpe>=1, PF>1, expectancy>0, return>0),
      2) rekam jejak paper-trading cukup (min hari & min trade), dan
      3) integrasi + kredensial broker riil ditambahkan secara sadar.
    """
    name = "idx-live-unconfigured"
    is_live = True

    def _refuse(self):
        raise BrokerError(
            "Broker IDX LIVE belum dikonfigurasi. Eksekusi uang-riil tidak "
            "tersedia: lolos-kan dulu gerbang backtest + rekam jejak paper, lalu "
            "colok broker riil secara sadar. (Prinsip risk-first: jangan bypass.)")

    def submit(self, order: Order) -> Fill:
        self._refuse()

    def account(self) -> Account:
        self._refuse()

    def positions(self) -> list[Position]:
        self._refuse()
