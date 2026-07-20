# quant-trading

Sistem analisa saham + (nanti) eksekusi otomatis untuk penggunaan pribadi.
**Prioritas utama: risk management, bukan mengejar target return.**

> Sistem ini adalah alat bantu analisa, bukan jaminan profit. Trading saham
> mengandung risiko kerugian modal.

## Status: Fase 1-2 selesai (fokus IDX)

| Fase | Modul | Status |
|------|-------|--------|
| 1 | Data ingestion + storage (SQLite) | ✅ selesai |
| 2 | Analysis engine + scoring + screener + signal logic | ✅ selesai |
| 3 | Backtesting engine + gerbang kesiapan live | ✅ selesai |
| 4 | Dashboard read-only (Streamlit) | ⬜ belum |
| 5 | Paper trading (Alpaca US) / notifikasi (IDX) | ⬜ belum |
| 6 | Live trading (HANYA setelah konfirmasi eksplisit user) | ⬜ belum |

## Struktur

```
quant/
  config.py            # SEMUA parameter risiko & threshold terpusat (mudah di-audit)
  universe.py          # konstituen LQ45 / IDX30 (perlu update berkala)
  data/
    storage.py         # SQLite: tabel ohlcv + ingestion_log (upsert idempotent)
    ingestion.py       # provider yfinance (.JK utk IDX); interface modular utk provider lain
  analysis/
    indicators.py      # EMA/RSI/MACD/BB/ATR + MFI/OBV/CMF/VWAP/A-D + volume spike
    scoring.py         # skor komposit -100..+100, klasifikasi, alasan tekstual
    screener.py        # filter likuiditas -> watchlist otomatis + deteksi tier spekulatif
    signals.py         # BUY/SL/TP + position sizing (risk-first, stop loss WAJIB)
scripts/
  ingest.py            # CLI: ambil data historis
  analyze.py           # CLI: watchlist + trade plan (read-only, tidak eksekusi)
tests/
  test_pipeline.py     # uji indikator/scoring/sizing dgn data sintetis (tanpa network)
```

## Cara pakai

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Ambil data 2 tahun untuk LQ45
python -m scripts.ingest --index LQ45 --period 2y

# 2. Analisa -> watchlist + trade plan (capital dalam Rupiah)
python -m scripts.analyze --capital 100000000 --top 15

# 3. Backtest + gerbang kesiapan live (simpan hasil ke data/backtests/)
python -m scripts.backtest --capital 100000000 --fee-bps 20

# uji cepat tanpa network
python -m tests.test_pipeline
python -m tests.test_backtest
```

## Temuan backtest awal (PENTING)

Dengan parameter DEFAULT pada data LQ45 ~2 tahun: strategi **RUGI**
(win rate ~30%, profit factor 0,60, Sharpe negatif, total return ~ −11%).
Ini hasil yang diharapkan dari rule-set naif pertama — dan justru inilah guna
backtesting: mencegah deploy uang riil untuk strategi tanpa edge.

Gerbang `live_readiness()` otomatis **memblokir live** selama profit factor < 1,
expectancy ≤ 0, total return ≤ 0, atau Sharpe < 1. Perbaikan strategi harus lewat
**walk-forward / out-of-sample**, bukan curve-fitting parameter ke satu periode.

## Aturan sinyal (watchlist utama)

BUY hanya jika **SEMUA** terpenuhi:
1. Skor komposit ≥ 60
2. Konfirmasi ≥ 2 kategori indikator berbeda (**trend + money flow**), bukan 1 indikator
3. Volume hari itu ≥ 1.5× rata-rata 20 hari
4. Harga di atas EMA50 (tren menengah naik)

Setiap BUY otomatis menghasilkan:
- **Stop loss WAJIB** berbasis ATR (default entry − 2×ATR)
- **Take profit** berbasis risk-reward (default min 1:2), bukan target harian
- **Position sizing** dihitung dari jarak ke stop loss & batas risiko per trade (default 1%)

## Risk management (non-negotiable, di `config.py`)

- Risiko per trade: 1% modal (hard cap 2%)
- Circuit breaker: −3%/hari stop harian, −7%/minggu stop total + review manual
- Max posisi terbuka: 5 (utama) + 2 (spekulatif), dihitung terpisah
- Tier spekulatif ("gorengan"): alokasi total ≤5% modal, risiko/trade ≤0,3%,
  stop lebih ketat, diberi label `SPECULATIVE - HIGH MANIPULATION RISK`
- Syarat sebelum live: backtest wajib, paper ≥60 hari & ≥30 trade tercatat

## Caveat penting (harus ditangani di fase berikutnya)

1. **Kalibrasi threshold** (skor 60, dsb.) belum divalidasi statistik — itu tugas
   Fase 3 (backtesting pada data riil 2-3 tahun). Jangan tuning ke data sintetis.
2. **Free float** tidak tersedia dari yfinance. Untuk sekarang syarat free float
   ≥15% dijaga tidak langsung lewat universe LQ45/IDX30. Perlu sumber data manual.
3. **Konstituen indeks berubah** (IDX evaluasi LQ45 tiap Feb & Agu). Daftar di
   `universe.py` adalah snapshot manual dan HARUS ditinjau berkala.
4. **VWAP** di sini kumulatif atas seluruh data (referensi harian). VWAP intraday
   sejati (reset harian) perlu data intraday di fase eksekusi.

## Belum ada di fase ini

Backtesting, dashboard, notifikasi Telegram/email, eksekusi (Alpaca paper untuk US,
notifikasi untuk IDX), modul US. Arsitektur sudah dibuat modular agar mudah disambung.
