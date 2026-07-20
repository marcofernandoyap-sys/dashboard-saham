"""
Konfigurasi terpusat untuk sistem quant trading.

SEMUA parameter risiko & threshold sinyal ada di sini supaya:
  1. Gampang di-audit (risk management adalah prioritas #1).
  2. Tidak ada "magic number" tersebar di seluruh kode.
  3. Backtest bisa memvariasikan parameter dari satu tempat.

Prinsip non-negotiable (lihat spec proyek):
  - Setiap posisi WAJIB punya stop loss.
  - Risiko per trade dibatasi persentase kecil dari modal.
  - Ada circuit breaker drawdown harian & mingguan.
  - Tier spekulatif ("gorengan") punya batasan jauh lebih ketat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "market.db"
LOG_DIR = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Parameter indikator teknikal
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IndicatorParams:
    ema_periods: tuple[int, ...] = (9, 21, 50, 200)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    mfi_period: int = 14
    cmf_period: int = 20
    obv_slope_window: int = 20          # jendela untuk cek tren OBV
    volume_avg_window: int = 20
    volume_spike_mult: float = 2.0      # volume > 2x rata-rata 20 hari = anomali
    atr_period: int = 14


# ---------------------------------------------------------------------------
# Parameter sinyal (watchlist utama)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalParams:
    # Skor komposit dinormalisasi ke -100..+100
    buy_score_threshold: float = 60.0
    strong_buy_score_threshold: float = 80.0
    sell_score_threshold: float = -60.0
    strong_sell_score_threshold: float = -80.0

    # Konfirmasi entry: butuh minimal 2 KATEGORI indikator berbeda (trend + money flow)
    min_indicator_categories: int = 2
    volume_confirm_mult: float = 1.5    # volume hari itu > 1.5x rata-rata 20 hari
    require_above_ema: int = 50         # harga di atas EMA50 = tren menengah naik

    # Risk-reward untuk take profit (BUKAN target harian tetap)
    min_risk_reward: float = 2.0        # minimum 1:2
    atr_stop_mult: float = 2.0          # stop loss = entry - 2*ATR (default)


# ---------------------------------------------------------------------------
# Risk management (WAJIB, NON-NEGOTIABLE)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskParams:
    # Watchlist utama
    max_risk_per_trade_pct: float = 0.01        # 1% modal per trade (batas atas 2%)
    max_risk_per_trade_hard_cap_pct: float = 0.02
    max_open_positions_main: int = 5

    # Circuit breaker
    max_daily_drawdown_pct: float = 0.03        # -3%/hari => stop trading hari itu
    max_weekly_drawdown_pct: float = 0.07       # -7%/minggu => stop total, review manual

    # Tier spekulatif / "gorengan" (JAUH lebih ketat)
    spec_total_allocation_cap_pct: float = 0.05  # maks 5% total modal utk seluruh tier
    spec_risk_per_trade_pct: float = 0.003       # maks 0.3% modal per trade
    max_open_positions_spec: int = 2

    # Syarat sebelum boleh live
    min_paper_trading_days: int = 60
    min_recorded_trades: int = 30
    require_backtest_before_live: bool = True


# ---------------------------------------------------------------------------
# Screening likuiditas (seleksi watchlist otomatis)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScreenParamsIDX:
    min_avg_daily_volume: int = 1_000_000       # lembar/hari
    min_avg_daily_value_idr: float = 5e9        # Rp 5 miliar/hari (alternatif volume)
    min_free_float_pct: float = 0.15            # >=15% (data free float sering manual)
    watchlist_top_n: int = 25


@dataclass(frozen=True)
class ScreenParamsUS:
    min_avg_daily_volume: int = 1_000_000
    min_market_cap_usd: float = 2e9
    min_price_usd: float = 10.0
    watchlist_top_n: int = 25


# ---------------------------------------------------------------------------
# Kriteria deteksi tier spekulatif ("gorengan")
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SpeculativeDetection:
    volume_spike_mult: float = 5.0      # volume > 5x rata-rata 20 hari
    open_gap_pct: float = 0.05          # gap pembukaan > 5% vs close sebelumnya
    freq_spike_mult: float = 3.0        # frekuensi transaksi > 3x rata-rata (jika ada data)
    warning_label: str = "SPECULATIVE - HIGH MANIPULATION RISK"


@dataclass(frozen=True)
class Settings:
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    signal: SignalParams = field(default_factory=SignalParams)
    risk: RiskParams = field(default_factory=RiskParams)
    screen_idx: ScreenParamsIDX = field(default_factory=ScreenParamsIDX)
    screen_us: ScreenParamsUS = field(default_factory=ScreenParamsUS)
    speculative: SpeculativeDetection = field(default_factory=SpeculativeDetection)

    disclaimer: str = (
        "Sistem ini adalah alat bantu analisa, bukan jaminan profit. "
        "Trading saham mengandung risiko kerugian modal."
    )


SETTINGS = Settings()
