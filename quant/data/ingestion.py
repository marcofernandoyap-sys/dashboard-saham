"""
Data ingestion via yfinance.

- IDX: ticker pakai suffix .JK (contoh: BBCA.JK).
- US : ticker biasa (contoh: AAPL).

yfinance di-import secara lazy supaya modul lain (indikator, scoring, test math)
tetap bisa dipakai tanpa yfinance/network terpasang.

Desain modular: `MarketDataProvider` adalah interface abstrak. Sekarang ada
`YFinanceProvider`; provider lain (Alpha Vantage, Polygon, RTI scrape) bisa
ditambahkan tanpa mengubah kode pemanggil.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from quant.data.storage import Storage


def _normalize(df: pd.DataFrame, ticker: str, market: str) -> pd.DataFrame:
    """Ubah output yfinance jadi skema kanonik kita."""
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    # yfinance kadang mengembalikan MultiIndex kolom saat 1 ticker; ratakan.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    rename = {
        "Date": "date", "Datetime": "date",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
    }
    df = df.rename(columns=rename)
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]

    df["ticker"] = ticker
    df["market"] = market
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    cols = ["ticker", "market", "date", "open", "high", "low",
            "close", "adj_close", "volume"]
    df = df[cols].dropna(subset=["close"])
    return df


class MarketDataProvider(ABC):
    @abstractmethod
    def fetch(self, ticker: str, period: str = "3y",
              interval: str = "1d") -> pd.DataFrame:
        ...


class YFinanceProvider(MarketDataProvider):
    def __init__(self, market: str = "IDX"):
        self.market = market

    def fetch(self, ticker: str, period: str = "3y",
              interval: str = "1d") -> pd.DataFrame:
        import yfinance as yf  # lazy import

        raw = yf.download(
            ticker, period=period, interval=interval,
            auto_adjust=False, progress=False, threads=False,
        )
        return _normalize(raw, ticker, self.market)


def ingest(tickers: list[str], provider: MarketDataProvider,
           storage: Storage, period: str = "3y",
           interval: str = "1d") -> dict[str, int]:
    """
    Ambil & simpan data untuk banyak ticker. Return {ticker: rows_written}.
    Kegagalan satu ticker tidak menghentikan yang lain (dicatat rows=-1).
    """
    results: dict[str, int] = {}
    market = getattr(provider, "market", "UNKNOWN")
    for t in tickers:
        try:
            df = provider.fetch(t, period=period, interval=interval)
            n = storage.upsert_ohlcv(df)
            start = df["date"].min() if not df.empty else None
            end = df["date"].max() if not df.empty else None
            storage.log_ingestion(t, market, n, start, end)
            results[t] = n
        except Exception as exc:  # noqa: BLE001 - kita mau lanjut ke ticker lain
            print(f"[ingest] GAGAL {t}: {exc}")
            results[t] = -1
    return results
