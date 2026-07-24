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

    # Risk-reward untuk take profit (BUKAN target harian tetap).
    # RR 3.0 & ATR 3.0 dipilih karena STABIL terpilih di 13/13 & 10/13 fold
    # walk-forward out-of-sample (bukan curve-fit ke satu periode).
    min_risk_reward: float = 3.0        # minimum 1:3
    atr_stop_mult: float = 3.0          # stop loss = entry - 3*ATR (default)


# ---------------------------------------------------------------------------
# Trailing stop (perubahan STRUKTURAL cara EXIT)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrailingStopParams:
    """
    Chandelier-style trailing stop untuk membiarkan pemenang berjalan.

    Stop loss AWAL tetap WAJIB & tidak berubah (entry - atr_stop_mult*ATR).
    Setelah profit >= activate_r (kelipatan risiko awal), stop mulai "naik"
    mengikuti: (harga tertinggi sejak entry) - trail_atr_mult * ATR.
    Stop HANYA boleh naik, tidak pernah turun (ratchet).

    KAUSAL: level trailing untuk hari d ditentukan dari data <= d-1 (high & ATR
    kemarin), lalu low hari d diuji terhadapnya -> tanpa lookahead.

    use_take_profit_cap=False -> take profit fixed DIMATIKAN saat trailing aktif,
    supaya hipotesis "TP fixed memotong pemenang" bisa diuji langsung: exit hanya
    lewat trailing stop atau time stop.

    HASIL WALK-FORWARD: trailing (konfigurasi ini) MEMPERBURUK OOS dibanding TP
    fixed 1:3 (PF 0.92 vs 1.16, MDD -27% vs -11%). Ternyata trailing kena
    whipsaw di pullback normal & melepas pemenang ekor-panjang yang justru
    ditangkap bersih oleh TP 1:3. Karena itu default = NONAKTIF. Kode & param
    tetap ada untuk eksperimen lanjutan (mis. aktivasi lebih longgar).
    """
    enabled: bool = False
    activate_r: float = 1.0         # mulai trailing setelah untung >= 1R
    trail_atr_mult: float = 3.0     # jarak trailing = 3*ATR dari puncak
    use_take_profit_cap: bool = False


# ---------------------------------------------------------------------------
# Filter regime pasar (perubahan STRUKTURAL, bukan tuning parameter)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegimeParams:
    """
    Hanya izinkan entry saat pasar (indeks acuan) dalam tren naik: harga indeks
    di atas EMA-nya. Tujuan: hindari trading long counter-trend di pasar turun
    (mis. 2022-2026) yang menghancurkan hasil out-of-sample.
    """
    enabled: bool = True
    index_ticker: str = "^JKSE"     # IHSG (yfinance)
    ema_period: int = 200


# ---------------------------------------------------------------------------
# Filter kekuatan relatif / Relative Strength (perubahan STRUKTURAL)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RSParams:
    """
    Hanya izinkan entry pada saham yang MENGUNGGULI indeks (outperform IHSG)
    selama jendela `lookback`: ROC_saham(lookback) - ROC_indeks(lookback) > min_rs.

    Tujuan: di dalam pasar yang sudah bullish (lolos filter regime), pilih
    pemimpin (leaders) bukan pengikut (laggards) -> mempertajam edge tipis.

    KAUSAL: ROC di tanggal d hanya memakai close(d) & close(d-lookback), keduanya
    sudah diketahui saat d -> tanpa lookahead. Filter ini TIDAK mengubah skor
    komposit (hanya gerbang entry), jadi cache skor walk-forward tetap valid.

    HASIL WALK-FORWARD: dengan lookback dipilih PER FOLD di in-sample (OOS-blind),
    RS MENINGKATKAN OOS secara nyata vs baseline regime: PF 1.16->1.38,
    expectancy +0.10->+0.23 R, Sharpe 0.20->0.54, total return +9.6%->+33.9%,
    max drawdown ~sama (-11.0% vs -11.3%). Ini BUKAN curve-fit (pemilihan lookback
    tak pernah melihat data uji). Karena itu default = AKTIF.

    lookback=252 dipilih karena: (1) paling sering terpilih in-sample (5/13 fold,
    modal), dan (2) ~ jendela momentum 12 bulan klasik. Bukan nilai yang dicocokkan
    ke OOS. Lawan: trailing stop DITOLAK karena memperburuk OOS.
    """
    enabled: bool = True
    index_ticker: str = "^JKSE"     # sama dgn regime (IHSG)
    lookback: int = 252             # ~12 bulan bursa (momentum klasik)
    min_rs: float = 0.0             # selisih ROC minimal (0 = sekadar unggul)


# ---------------------------------------------------------------------------
# Kandidat perbaikan struktural #4 (target: naikkan Sharpe -> lolos gerbang live)
# Semua DEFAULT NONAKTIF/NETRAL: hanya diuji lewat walk-forward (OOS-blind).
# Diadopsi HANYA kalau memperbaiki out-of-sample; kalau tidak -> ditolak
# (persis metodologi trailing stop [ditolak] & RS [diterima]).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VolFilterParams:
    """
    Kandidat #4A: tolak entry saat volatilitas saham (ATR%) berada di persentil
    TINGGI relatif histori dirinya sendiri (hindari masuk saat chop/euforia).

    KAUSAL: peringkat persentil di tanggal d dihitung dari ATR%/close pada
    jendela <= d saja. Ini gerbang ENTRY -> tidak mengubah skor komposit ->
    cache skor walk-forward tetap valid.

    max_percentile=1.0 berarti TIDAK memblokir apa pun (netral/off).
    """
    enabled: bool = False
    atr_pct_window: int = 252       # jendela peringkat persentil rolling
    max_percentile: float = 1.0     # 1.0 = off; mis. 0.8 = tolak 20% paling bergejolak


@dataclass(frozen=True)
class ScaleOutParams:
    """
    Kandidat #4C: ambil sebagian posisi di trigger_r (mis. +1.5R), sisanya
    dibiarkan jalan; stop dinaikkan ke breakeven -> kurangi variance hasil.

    KAUSAL: trigger diuji dgn high hari itu (sama seperti take-profit fixed).
    Stop-ke-BE berlaku untuk bar berikutnya (ratchet), tanpa lookahead.

    enabled=False -> perilaku exit lama (satu TP penuh), netral.
    """
    enabled: bool = False
    trigger_r: float = 1.5          # ambil sebagian saat untung >= 1.5R
    fraction: float = 0.5           # porsi yang dijual (0.5 = separuh)
    move_stop_breakeven: bool = True


@dataclass(frozen=True)
class BreadthParams:
    """
    Kandidat struktural #8 (non-price #1): filter LEBAR PASAR (market breadth).

    Motivasi: filter regime (#1) hanya melihat indeks (IHSG > EMA200). Tapi indeks
    bisa ditopang 2-3 saham raksasa saja sementara mayoritas saham melemah — rally
    "sempit" yang rapuh. Breadth mengukur PARTISIPASI: berapa persen universe yang
    harganya di atas EMA-nya sendiri. Ini INFORMASI BARU yang tidak ditangkap
    regime maupun RS (keduanya lolos OOS justru karena menambah konteks baru).

    Ini fitur NON-PRICE dalam arti "bukan price saham individual" — ia agregat
    lintas-universe. Tetap dihitung dari OHLCV yang SUDAH kita punya (tak ada
    dependdouble data baru, tak ada risiko lookahead seperti fundamental yfinance
    yang cuma snapshot terkini).

    KAUSAL: breadth di tanggal d = fraksi ticker dgn close(d) > EMA(d), keduanya
    diketahui saat d. Ini gerbang ENTRY -> tak mengubah skor komposit -> cache
    skor walk-forward tetap valid.

    min_breadth=0.0 -> tak pernah memblokir (netral/off).
    """
    enabled: bool = False
    ema_period: int = 50            # partisipasi thd tren menengah (kolom ema_50)
    min_breadth: float = 0.0        # 0 = off; mis. 0.4 = butuh >=40% universe sehat


@dataclass(frozen=True)
class KellySizingParams:
    """
    Kandidat struktural #7: sizing Kelly-fraksi ADAPTIF. Alih-alih risiko flat
    1% modal per trade, skala risiko naik/turun mengikuti EDGE terkini strategi
    (expektansi R dari trade yang SUDAH ditutup). Ide: perbesar saat sistem
    sedang "panas" (edge positif), perkecil saat "dingin"/rugi beruntun ->
    haluskan kurva ekuitas -> Sharpe naik.

    scale = clip(1 + kelly_fraction * rolling_expectancy_R, min_scale, max_scale)
      - rolling_expectancy_R = rata-rata r_multiple `lookback_trades` trade
        terakhir yang sudah ditutup SEBELUM entry ini (kausal; exit hari-d
        diproses sebelum entry hari-d -> bukan lookahead).
      - Feedback bersih: r_multiple TAK bergantung jumlah lembar, jadi sizing
        kita tidak mengubah sinyal edge yang dibacanya (tak ada loop liar).

    GERBANG RISIKO tetap: scale efektif di-clamp agar base_risk*scale TIDAK
    pernah > max_risk_per_trade_hard_cap_pct (2%). Jadi Kelly hanya boleh
    memperbesar sampai batas keras yang sama, tak lebih.

    kelly_fraction=0.0 -> scale selalu 1.0 (NETRAL, == baseline) meski enabled.
    enabled=False -> sizing lama (flat), 100% backward-compatible.
    """
    enabled: bool = False
    lookback_trades: int = 20       # jumlah trade tutup terakhir utk estimasi edge
    kelly_fraction: float = 0.0     # 0 = netral; makin besar makin agresif
    min_scale: float = 0.5          # lantai: jangan di bawah 0.5x risiko dasar
    max_scale: float = 2.0          # plafon: 2.0x = 2% = hard cap (bila base 1%)
    min_trades: int = 10            # sebelum cukup sampel -> pakai risiko dasar


@dataclass(frozen=True)
class CorrelationFilterParams:
    """
    Kandidat struktural #5: tolak entry baru kalau korelasinya dgn salah satu
    posisi TERBUKA sudah tinggi. Ide: kurangi klaster-risiko (misal semua bank
    bergerak bersama) -> return per unit-risiko (Sharpe) lebih baik TANPA
    memotong pemenang panjang (beda dari scale-out yang gagal).

    KAUSAL: korelasi dihitung dari return harian close-to-close pada jendela
    berakhir di d (tanggal entry). Return d SUDAH diketahui saat sinyal muncul.
    Ini gerbang ENTRY -> tidak mengubah skor komposit -> cache skor tetap valid.

    max_corr=1.0 -> tak pernah menolak (netral/off).
    """
    enabled: bool = False
    lookback: int = 63              # ~3 bulan return harian
    max_corr: float = 1.0           # 1.0 = off; mis. 0.7 = tolak bila corr > 0.7
    min_samples: int = 30           # minimal titik data valid; kalau kurang, lolos


# ---------------------------------------------------------------------------
# Risk management (WAJIB, NON-NEGOTIABLE)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskParams:
    # Watchlist utama
    max_risk_per_trade_pct: float = 0.01        # 1% modal per trade (batas atas 2%)
    max_risk_per_trade_hard_cap_pct: float = 0.02
    max_open_positions_main: int = 5

    # Kandidat #4B: cap bobot notional satu posisi thd ekuitas (batasi
    # konsentrasi -> haluskan kurva ekuitas). 1.0 = off (tak ada cap).
    max_position_weight: float = 1.0

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


# ---------------------------------------------------------------------------
# Notifikasi (Fase 5, IDX) — HANYA alert, TIDAK ada eksekusi order
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NotifyParams:
    """
    Parameter NON-RAHASIA untuk notifikasi harian. Kredensial (token bot
    Telegram / SMTP) TIDAK disimpan di sini — dibaca dari environment / .env
    saat runtime (lihat quant/notify/channels.py) supaya tak pernah ter-commit.

    Default aman: channel 'console' (dry-run) -> tak ada pesan terkirim sampai
    channel diisi eksplisit. send_when_empty=False -> tidak nge-spam saat tak ada
    sinyal actionable (hari tanpa sinyal itu normal & sehat).
    """
    channel: str = "console"            # console | telegram | email
    send_when_empty: bool = False       # kirim juga saat 0 sinyal actionable?
    dedup_enabled: bool = True          # jangan kirim digest identik 2x/hari
    include_blocked_signals: bool = True  # tampilkan sinyal yg diblokir regime/RS
    top_watchlist_in_digest: int = 5    # berapa baris watchlist di ringkasan


# ---------------------------------------------------------------------------
# Eksekusi order (Fase 5b paper / Fase 6 live) — GERBANG RISIKO NON-NEGOTIABLE
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutionParams:
    """
    Parameter NON-RAHASIA untuk layer eksekusi. Kredensial broker (Alpaca / broker
    IDX) TIDAK di sini — dibaca dari environment / .env saat runtime.

    Default AMAN & risk-first:
      - mode='paper'         : simulasi/paper saja; TIDAK ada uang riil.
      - broker='paper'       : PaperBroker lokal (zero-dep) — jalan untuk IDX.
      - allow_live=False      : gerbang uang riil MATI. Meski di-set True, order
        live tetap DITOLAK selama live_readiness (backtest) DIBLOKIR dan syarat
        paper-trading (min hari & min trade) belum terpenuhi. Ini SENGAJA: jangan
        pernah bypass gerbang risiko demi mengejar return.
      - require_paper_track_record=True : live butuh rekam jejak paper yang cukup.

    lot_size_idx=100: bursa IDX transaksi per-lot (100 lembar). Sizing dibulatkan
    KE BAWAH ke kelipatan lot supaya risiko tak pernah melebihi batas.
    """
    mode: str = "paper"                 # paper | live
    broker: str = "paper"               # paper | alpaca | idx  (idx=live, di-gate)
    allow_live: bool = False            # sakelar utama uang riil (tetap di-gate lagi)
    require_backtest_gate: bool = True  # live wajib lolos registry.live_readiness
    require_paper_track_record: bool = True
    lot_size_idx: int = 100
    default_fee_bps: float = 20.0       # konsisten dengan backtest


@dataclass(frozen=True)
class Settings:
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    signal: SignalParams = field(default_factory=SignalParams)
    trailing: TrailingStopParams = field(default_factory=TrailingStopParams)
    regime: RegimeParams = field(default_factory=RegimeParams)
    rs: RSParams = field(default_factory=RSParams)
    vol_filter: VolFilterParams = field(default_factory=VolFilterParams)
    scaleout: ScaleOutParams = field(default_factory=ScaleOutParams)
    corr_filter: CorrelationFilterParams = field(
        default_factory=CorrelationFilterParams)
    kelly: KellySizingParams = field(default_factory=KellySizingParams)
    breadth: BreadthParams = field(default_factory=BreadthParams)
    risk: RiskParams = field(default_factory=RiskParams)
    screen_idx: ScreenParamsIDX = field(default_factory=ScreenParamsIDX)
    screen_us: ScreenParamsUS = field(default_factory=ScreenParamsUS)
    speculative: SpeculativeDetection = field(default_factory=SpeculativeDetection)
    notify: NotifyParams = field(default_factory=NotifyParams)
    execution: ExecutionParams = field(default_factory=ExecutionParams)

    disclaimer: str = (
        "Sistem ini adalah alat bantu analisa, bukan jaminan profit. "
        "Trading saham mengandung risiko kerugian modal."
    )


SETTINGS = Settings()
