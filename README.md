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
| 3 | Backtesting + walk-forward + filter regime & RS + gerbang live | ✅ selesai |
| 4 | Dashboard read-only (Streamlit) | ✅ selesai |
| 5a | Notifikasi/alert harian IDX (console/telegram/email) | ✅ selesai |
| 5b | Eksekusi paper: PaperBroker lokal (IDX) + Alpaca (US) | ✅ selesai |
| 6 | Live trading (uang riil) — jalur ada, **di-gate keras** | 🔒 di-gate |

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
  dashboard.py         # Streamlit read-only: status/regime, watchlist, plan, backtest
  notify.py            # CLI alert harian IDX (console/telegram/email); TIDAK eksekusi
  trade.py             # CLI eksekusi: paper default; jalur live di-gate keras
  paper_daily.py       # CLI workflow paper harian (refresh+trade+log); cron-friendly
  experiment_structural.py  # walk-forward #4A/B/C (vol/weight/scaleout); OOS-blind
  experiment_corr.py   # walk-forward #5 filter korelasi; OOS-blind
quant/
  notify/
    digest.py          # bangun DailyReport + format teks + signature dedup (murni)
    state.py           # dedup: simpan signature digest terakhir (data/notify_state.json)
    channels.py        # Console/Telegram/Email (stdlib saja) + loader .env + factory
  execution/
    broker.py          # kontrak Broker + Order/Fill/Position/Account; IDX-live placeholder (menolak)
    paper.py           # PaperBroker lokal (simulasi, zero-dep) — jalan utk IDX
    alpaca.py          # AlpacaBroker US (paper default) via urllib, tanpa SDK
    journal.py         # catat fill (SQLite) + rekam jejak paper utk gerbang live
    engine.py          # ExecutionEngine: tegakkan gerbang risiko SEBELUM order live
tests/
  test_pipeline.py     # uji indikator/scoring/sizing dgn data sintetis (tanpa network)
  test_notify.py       # uji digest + dedup + console channel (tanpa network)
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

# 4. Walk-forward (optimasi in-sample -> uji out-of-sample; anti curve-fitting)
python -m scripts.walkforward --capital 100000000 --is-days 504 --oos-days 126

# 4b. Eksperimen struktural (baseline vs kandidat #4A/#4B/#4C; adopsi jujur OOS)
python -m scripts.experiment_structural
python -m scripts.experiment_corr        # #5 correlation filter (rejected)
python -m scripts.experiment_weekly      # #6 timeframe weekly W-FRI (rejected)
python -m scripts.experiment_kelly       # #7 Kelly-fraction sizing (rejected)
python -m scripts.experiment_breadth     # #8 market breadth filter (non-price)

# uji cepat tanpa network
python -m tests.test_pipeline
python -m tests.test_backtest

# 5. Dashboard read-only (status regime, watchlist, trade plan, backtest)
streamlit run scripts/dashboard.py

# 6. Notifikasi/alert harian IDX — DEFAULT dry-run ke console (tak kirim apa-apa)
python -m scripts.notify --capital 100000000
python -m scripts.notify --channel telegram      # butuh kredensial di .env
python -m scripts.notify --channel telegram --force   # abaikan dedup
python -m tests.test_notify                       # uji cepat tanpa network

# 7. Eksekusi PAPER (simulasi, tanpa uang riil) — default broker lokal IDX
python -m scripts.trade --status                  # lihat gerbang + akun, tak order
python -m scripts.trade --broker paper            # eksekusi sinyal actionable (paper)
python -m scripts.trade --broker alpaca           # paper Alpaca US (butuh kredensial)
python -m scripts.trade --live                    # minta uang-riil -> DITOLAK gerbang
python -m tests.test_execution                    # uji cepat tanpa network

# 8. Paper HARIAN (bangun rekam-jejak ≥60 hari / ≥30 trade untuk gate paper)
python -m scripts.paper_daily                     # refresh data + paper + log harian
python -m scripts.paper_daily --skip-ingest       # pakai data existing (offline)
```

Dashboard hanya MENAMPILKAN analisa (tidak ada tombol eksekusi order). Status
regime pasar & gerbang kesiapan live ditampilkan menonjol supaya keputusan
manusia sadar konteks risiko.

### Notifikasi harian (Fase 5a) — ALERT, bukan eksekusi

`scripts/notify.py` membangun digest harian (sinyal BUY yang lolos SEMUA gerbang
signal + regime + RS, plus watchlist teratas) dan mengirimnya. **Tidak ada
eksekusi order di mana pun** — ini murni pemberitahuan; keputusan & order manual.

- **Default aman**: channel `console` = dry-run (cetak saja, tak pernah gagal,
  tak mengirim). Kirim nyata hanya kalau channel diset ke `telegram`/`email`.
- **Tanpa dependency baru**: Telegram lewat `urllib`, email lewat `smtplib`
  (stdlib). Kredensial dibaca dari environment / file `.env` (di-gitignore).
- **Dedup**: digest identik (signature SHA256 dari tanggal + regime + daftar
  sinyal) tidak dikirim dua kali; pakai `--force` untuk kirim ulang.
- **Diam kalau kosong**: kalau tak ada sinyal actionable, tidak mengirim
  (kecuali `notify.send_when_empty=True` di config).

Setup kredensial: `cp .env.example .env` lalu isi. Kunci yang dibaca:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (telegram); `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` (email).

### Eksekusi (Fase 5b paper & Fase 6 live) — GERBANG RISIKO tak boleh di-bypass

Semua order lewat satu jalur: `TradePlan → ExecutionEngine → Broker → Journal`.
Engine adalah penjaga: ia **menegakkan gerbang risiko sebelum order live**.

- **Default `paper`** (`scripts/trade.py` tanpa argumen): `PaperBroker` lokal —
  simulasi zero-dep, jalan untuk IDX sekarang. Isi order di harga acuan (close),
  fee tiap sisi, tolak kalau kas/posisi kurang. State di `data/paper_account.json`
  (di-gitignore). Dari sinilah **rekam jejak paper** terkumpul.
- **Alpaca (US, Fase 5b)**: `--broker alpaca`, default paper (uang virtual) via
  `urllib` (tanpa SDK). Alpaca **tidak** mendukung IDX. Kredensial dari `.env`
  (`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`).
- **Lot IDX**: `qty` BUY dibulatkan **ke bawah** ke kelipatan 100 supaya risiko
  tak pernah melar di atas batas; < 1 lot → tidak dieksekusi.

**Jalur LIVE (uang riil, Fase 6) sengaja di-gate keras.** Order live DITOLAK
selama salah satu ini benar:
1. `execution.allow_live=False` (sakelar default MATI), atau
2. gerbang backtest `live_readiness` DIBLOKIR (PF>1, expectancy>0, return>0,
   **Sharpe≥1** — saat ini Sharpe 0.46 → DIBLOKIR), atau
3. rekam jejak paper belum cukup (`min_paper_trading_days=60`,
   `min_recorded_trades=30`).

Broker IDX-live nyata **belum** disambungkan: `LiveBrokerNotConfigured` adalah
placeholder yang menolak semua operasi. Interface `Broker` sudah disiapkan agar
broker riil bisa "dicolok" nanti tanpa mengubah engine — **tapi hanya setelah**
ketiga gerbang di atas lolos secara jujur. Ini penerapan langsung prinsip #1
proyek: *risk management, jangan pernah bypass gerbang demi mengejar return.*
Cek status kapan saja: `python -m scripts.trade --status`.

### Workflow paper harian (bangun rekam jejak untuk gate paper)

Gate paper (`min_paper_trading_days=60`, `min_recorded_trades=30`) butuh waktu
KALENDER, bukan compute. `scripts/paper_daily.py` menjalankan siklus lengkap
tiap hari kerja: refresh data → bangun sinyal → paper broker → journal → log
harian ke `data/paper/YYYY-MM-DD.log`. Aman untuk cron (exit 0 kalau tidak ada
sinyal atau hari libur — bukan error).

Cron macOS/Linux (tiap hari kerja 17:15 WIB / 10:15 UTC, sekitar 1 jam setelah
close IDX):

```
15 10 * * 1-5 cd /Users/crm/quant-trading && \
    .venv/bin/python -m scripts.paper_daily >> data/paper/cron.log 2>&1
```

Atau launchd (macOS, direkomendasikan di macOS modern — cron sudah tidak
dianjurkan): plist siap-pakai ada di `scripts/launchd/com.marco.quant-paper.plist`.
Install singkat:

```sh
cp scripts/launchd/com.marco.quant-paper.plist \
   ~/Library/LaunchAgents/com.marco.quant-paper.plist
launchctl load ~/Library/LaunchAgents/com.marco.quant-paper.plist
launchctl list | grep com.marco.quant-paper       # verifikasi
```

Detail (uji manual, uninstall, catatan Mac tidur, dsb.) di
`scripts/launchd/README.md`.

Alternatif paling sederhana: jalankan manual tiap sore. Yang penting:
**konsisten**, karena gate paper mengukur hari + trade, bukan intensitas.
Kalau tidak ada sinyal hari itu → hari tetap tercatat tapi tanpa trade baru;
kalau ada → journal bertambah.

## Temuan backtest awal (PENTING)

Dengan parameter DEFAULT pada data LQ45 ~2 tahun: strategi **RUGI**
(win rate ~30%, profit factor 0,60, Sharpe negatif, total return ~ −11%).
Ini hasil yang diharapkan dari rule-set naif pertama — dan justru inilah guna
backtesting: mencegah deploy uang riil untuk strategi tanpa edge.

Gerbang `live_readiness()` otomatis **memblokir live** selama profit factor < 1,
expectancy ≤ 0, total return ≤ 0, atau Sharpe < 1. Perbaikan strategi harus lewat
**walk-forward / out-of-sample**, bukan curve-fitting parameter ke satu periode.

### Hasil walk-forward (data LQ45 ~10 tahun, 2016-2026)

Optimasi parameter (threshold skor / ATR-stop / risk-reward) dilakukan PER FOLD
pada jendela in-sample 2 tahun, lalu diuji pada out-of-sample 6 bulan berikutnya
yang belum pernah dilihat. Agregat 13 fold OOS:

| Metrik | Nilai |
|--------|-------|
| Jumlah trade OOS | 249 |
| Win rate | 34,9% |
| Profit factor | 0,88 |
| Expectancy | −0,02 R |
| Max drawdown | −34,1% |
| Sharpe | −0,31 |
| Total return OOS | −18,9% |

**Kesimpulan jujur: rule-set awal TIDAK punya edge yang bertahan out-of-sample.**
Beberapa fold awal (2019-2022) positif, tapi hancur 2022-2026 — ciri khas
parameter yang cocok kebetulan, bukan edge nyata. Artinya masalahnya
**struktural (logika sinyal), bukan kalibrasi parameter.**

### Perbaikan struktural #1: filter regime pasar (IHSG > EMA200)

Perubahan LOGIKA (bukan tuning): entry long hanya diizinkan saat IHSG (`^JKSE`)
di atas EMA200-nya. Tujuan: berhenti melawan tren di pasar turun. Walk-forward
diulang identik, hanya ditambah filter ini:

| Metrik OOS agregat | Tanpa regime | **Dengan regime** |
|--------------------|--------------|-------------------|
| Jumlah trade | 249 | 198 |
| Win rate | 34,9% | 34,3% |
| Profit factor | 0,88 | **1,16** |
| Expectancy | −0,02 R | **+0,10 R** |
| Max drawdown | −34,1% | **−11,0%** |
| Sharpe | −0,31 | **+0,20** |
| Total return OOS | −18,9% | **+9,6%** |

Filter regime memindahkan strategi dari "no edge" ke edge tipis, dan yang paling
penting **memangkas max drawdown ~3x** (menghindari periode terburuk). Parameter
terpilih juga jadi lebih stabil: `min_risk_reward` = 3,0 di SEMUA 13 fold,
`atr_stop_mult` = 3,0 di 10/13 fold. Kestabilan lintas-fold ini (bukan cocok ke
satu periode) yang membuatnya kredibel.

Setelah temuan ini, **default diubah ke RR 3,0 / ATR 3,0** (nilai yang stabil di
walk-forward, bukan curve-fit). Backtest full-period + regime + default baru:
PF 1,33, expectancy +0,20 R, total return +34,1%, MDD −12,1%, Sharpe 0,42.

### Perbaikan struktural #2: trailing stop → DITOLAK (hasil negatif)

Hipotesis: TP fixed 1:3 memotong pemenang; trailing stop (chandelier 3×ATR,
aktif setelah +1R, TP fixed dimatikan) mestinya membiarkan pemenang berjalan.
Walk-forward menolak hipotesis ini:

| Metrik OOS agregat | Regime + TP fixed | Regime + trailing |
|--------------------|-------------------|-------------------|
| Profit factor | **1,16** | 0,92 |
| Expectancy | **+0,10 R** | +0,01 R |
| Max drawdown | **−11,0%** | −27,2% |
| Sharpe | **+0,20** | −0,12 |
| Total return OOS | **+9,6%** | −11,5% |

Trailing malah kena **whipsaw** di pullback normal dan melepas pemenang
ekor-panjang yang justru ditangkap bersih oleh TP 1:3; optimizer pun jadi
memilih stop awal ketat (1,5-2×ATR) yang overfit di in-sample. **Default trailing
= NONAKTIF.** Kode + test tetap ada (`TrailingStopParams`) untuk eksperimen
lanjutan (mis. aktivasi lebih longgar / trail lebih lebar). Ini contoh guna
walk-forward: ide yang "masuk akal" ternyata merugikan, dan ditolak sebelum
menyentuh uang riil.

### Perbaikan struktural #3: filter kekuatan relatif (RS vs IHSG) → DITERIMA

Hipotesis: di dalam pasar yang sudah bullish (lolos filter regime), beli
**pemimpin** bukan **pengikut** — hanya saham yang mengungguli IHSG
(`ROC_saham(lookback) − ROC_IHSG(lookback) > 0`). Berbeda dari lookback tunggal
yang dicocokkan ke satu periode, **lookback dipilih PER FOLD dari in-sample saja**
(kandidat 21/42/63/126/252 hari), lalu diuji di OOS yang belum pernah dilihat —
sehingga pemilihan lookback TIDAK bisa curve-fit ke data uji.

| Metrik OOS agregat | Regime saja | **Regime + RS** |
|--------------------|-------------|-----------------|
| Jumlah trade | 198 | 190 |
| Win rate | 34,3% | **40,5%** |
| Profit factor | 1,16 | **1,38** |
| Expectancy | +0,10 R | **+0,23 R** |
| Max drawdown | −11,0% | −11,3% |
| Sharpe | +0,20 | **+0,54** |
| Total return OOS | +9,6% | **+33,9%** |

RS **melipatgandakan** return OOS (~3,5×) dan Sharpe (~2,7×), menaikkan win rate &
expectancy, dengan max drawdown ~sama. Lookback yang dipilih in-sample condong ke
jendela panjang (252 hari terpilih 5/13 fold, modal; 126 & 42 masing-masing 3×),
dan optimizer **menghindari** lookback 63 yang jeblok di sweep naif — tanda
pemilihan IS menggeneralisasi ke OOS. Karena itu **default RS = AKTIF, lookback
252** (nilai modal in-sample ≈ momentum 12 bulan klasik, bukan cocokan ke OOS).

Backtest full-period + regime + RS(252): PF 1,38, expectancy +0,25 R, total return
+36,8%, MDD −10,4%, Sharpe 0,46 — lebih baik dari regime saja (+34,1%, MDD −12,1%)
sekaligus drawdown lebih kecil.

**CATATAN JUJUR / belum live-ready:** meski RS menaikkan Sharpe OOS ke 0,54 (dari
0,20), itu masih << 1,0 — gerbang `live_readiness()` tetap **memblokir** live
(benar). Edge kini lebih tebal tapi tetap dari ekor kanan yang jarang & besar
(khas trend-following). Langkah struktural #4 (filter volatilitas, cap bobot,
scale-out) diuji berikutnya untuk mengejar Sharpe ≥ 1,0 — hasilnya di bawah.

### Perbaikan struktural #4: filter volatilitas / cap bobot / scale-out → SEMUA DITOLAK

Untuk menaikkan Sharpe OOS ≥ 1,0 (agar gate live lolos), tiga kandidat diuji
WALK-FORWARD (nilainya dipilih PER FOLD dari in-sample, diuji di OOS yang belum
pernah dilihat — sama metodologi jujur seperti #2 trailing [ditolak] & #3 RS
[diterima]). Ketiganya berbagi cache skor: mereka gerbang entry/exit, bukan skor.

| Kandidat | Ide | Grid IS |
|----------|-----|---------|
| #4A vol_filter | tolak entry saat ATR% saham di persentil TINGGI (chop/euforia) | max_percentile ∈ {0,6; 0,7; 0,8; 0,9} |
| #4B max_position_weight | cap bobot notional satu posisi thd ekuitas | ∈ {0,2; 0,3; 0,5} |
| #4C scaleout | ambil 50% di trigger_r, stop sisanya → breakeven | trigger_r ∈ {1,0; 1,5; 2,0} |

Hasil OOS agregat (log lengkap: `data/experiments/structural_4_2026-07-21.log`):

| Metrik OOS agregat | baseline (regime+RS) | #4A vol_filter | #4B weight cap | #4C scaleout |
|--------------------|----------------------|----------------|----------------|--------------|
| Jumlah trade | 190 | 129 | 193 | 254 |
| Profit factor | 1,38 | 1,25 | 1,37 | **2,32** |
| Expectancy | +0,23 R | +0,19 R | +0,23 R | **+0,56 R** |
| Max drawdown | −11,3% | −9,9% | −11,2% | −11,3% |
| **Sharpe** | **+0,54** | +0,39 | +0,51 | +0,34 |
| Total return OOS | **+33,9%** | +18,2% | +31,1% | +16,9% |

Ketiganya **menurunkan Sharpe OOS** dan **total return OOS**. Skenario #4C paling
menyesatkan: PF & expectancy per-trade naik tinggi (2,32 dan +0,56 R) tapi memotong
compounding pemenang panjang → return berkurang setengah dan Sharpe turun. Ini
persis pelajaran yang sama seperti trailing (#2): kualitas per-trade ≠ kualitas
kurva ekuitas. #4B (cap 0,2 dipilih 12/13 fold — sangat stabil) hampir netral
tapi tetap sedikit mengurangi return tanpa perbaikan Sharpe. #4A memilih ambang
0,6-0,8 (juga stabil) tapi memangkas jumlah trade terlalu banyak.

**Verdikt: TOLAK semua kandidat #4.** Default tetap OFF/netral (`vol_filter.enabled=False`,
`max_position_weight=1.0`, `scaleout.enabled=False`) — perilaku live/backtest
tidak berubah. Kode kandidat DIPERTAHANKAN di repo (bisa diaktifkan lewat config
untuk riset lanjutan), tapi bukan bagian dari strategi default. Gate live tetap
DIBLOKIR dengan benar oleh Sharpe 0,46 < 1,0.

Pelajaran ulangan yang berharga: berkali-kali "ide masuk akal" (trailing, vol
filter, position cap, scale-out) gagal OOS. Ini justru bukti walk-forward bekerja
— mencegah kita men-live-kan strategi curve-fit.

### Perbaikan struktural #5: filter korelasi antar-posisi → DITOLAK

Hipotesis: sebagian besar drawdown datang dari klaster-risiko (mis. semua bank
di watchlist bergerak bersama saat sektor jatuh). Menolak entry baru saat
korelasinya dgn posisi TERBUKA sudah > ambang seharusnya menghaluskan kurva
ekuitas dan menaikkan Sharpe TANPA memotong pemenang panjang (beda dari
scale-out #4C yg gagal karena memotong compounding).

KAUSAL: korelasi dihitung dari return harian close-to-close pada jendela 63 hari
yang berakhir di tanggal entry (close d sudah diketahui saat sinyal muncul).
Ambang dipilih PER FOLD dari in-sample (kandidat 0,5/0,6/0,7/0,8), diuji di OOS
yg belum pernah dilihat.

Hasil OOS agregat (log: `data/experiments/structural_5_corr_2026-07-22.log`):

| Metrik OOS agregat | baseline (regime+RS) | + corr_filter |
|--------------------|----------------------|---------------|
| Jumlah trade | 190 | 193 |
| Profit factor | **1,38** | 1,31 |
| Expectancy | +0,23 R | +0,19 R |
| Max drawdown | **−11,3%** | −15,3% |
| **Sharpe** | **+0,54** | +0,44 |
| Total return OOS | **+33,9%** | +26,1% |

**Semua metrik OOS memburuk**, termasuk MDD (yang seharusnya JUSTRU menjadi
manfaat utama filter ini) yang malah bertambah buruk dari −11,3% ke −15,3%.
Ambang yang terpilih in-sample dominan di 0,5 (7/13 fold — sangat stabil), tapi
generalisasi ke OOS gagal. Diagnosis: pada universe LQ45 dengan max_open=5,
seleksi score sudah bagus; menolak "kandidat mirip" ternyata membuang lebih
banyak PEMENANG (karena leader sektor bergerak sama) daripada menghindari
CLUSTER LOSS.

**Verdikt: TOLAK.** Default tetap `corr_filter.enabled=False` (netral). Kode
dipertahankan (mekanisme diuji unit — lihat `tests/test_backtest.py`), tapi
bukan bagian strategi default. Gate live tetap DIBLOKIR dengan benar oleh
Sharpe 0,46 < 1,0.

### Perbaikan struktural #6: timeframe mingguan (W-FRI bar) → DITOLAK

Hipotesis: trend-following biasanya lebih bersih di weekly (noise harian
berkurang) → Sharpe OOS bisa naik menembus gate live. Ini perubahan STRUKTURAL
besar (pipeline resample + settings variant + anualisasi 52/tahun), bukan tuning
parameter, jadi diuji dengan protokol yang sama seperti #1-#5.

Pipeline uji:
- OHLCV daily di-resample W-FRI (open=first, high=max, low=min, close=last,
  volume=sum) via `quant/data/resample.py`.
- ema_periods DIPERTAHANKAN (9,21,50,200) supaya kolom hardcode
  `ema_21/50/200` di scoring tetap valid (weekly EMA200 = ~4 tahun, konvensi
  siklus panjang yang valid).
- RS lookback 252d→52w, ATR%-window 252d→52w, volume/OBV window 20d→4w,
  max_hold 60d→12w, `periods_per_year=52` untuk anualisasi Sharpe/CAGR
  (via `BacktestConfig`).
- Warmup walk-forward 210 minggu (~4 tahun) — cukup EMA200 weekly.
- Grid RS lookback disesuaikan: [4, 8, 13, 26, 52] minggu.

Hasil OOS agregat (log: `data/experiments/structural_6_weekly_2026-07-22.log`):

| Metrik OOS agregat | baseline (daily) | weekly |
|--------------------|------------------|--------|
| Jumlah trade | 190 | 55 |
| Profit factor | **1,38** | 0,55 |
| Expectancy | +0,23 R | −0,24 R |
| Max drawdown | **−11,3%** | −15,9% |
| **Sharpe (anualisasi benar)** | **+0,54** | **−0,82** |
| Total return OOS | **+33,9%** | −14,3% |

**Semua metrik OOS memburuk secara ekstrim** — expectancy jadi NEGATIF dan
Sharpe jatuh 1,36 poin. Hipotesis "weekly lebih bersih" DITOLAK oleh data pada
universe LQ45 kita. Diagnosis: signaling engine dirancang untuk konfirmasi
volume+trend HARIAN; dengan hanya ~55 trade OOS di 7 fold, edge kecil harian
tidak tersulih ke edge lebih besar mingguan — malah hilang. Kemungkinan
signaling engine untuk weekly butuh redesign (bukan sekadar reparameter), yang
di luar cakupan eksperimen ini.

Param yang stabil terpilih di IS (buy_score=50 di 5/7 fold, atr_stop=1.5 di 5/7
fold, rs_lookback=52w di 3/7 fold) menunjukkan search tidak "acak" — mereka
konvergen pada konfigurasi ketat yang tetap gagal generalisasi. Ini sinyal
struktural, bukan kegagalan tuning.

**Verdikt: TOLAK.** Default tetap timeframe HARIAN. Kode resample &
`periods_per_year` dipertahankan (unit-tested — lihat `tests.test_backtest.
test_weekly_resample_ohlcv`) untuk siap-dipakai kalau kelak ada signaling
engine khusus weekly. Gate live tetap DIBLOKIR benar oleh Sharpe daily 0,54 < 1,0.

### Perbaikan struktural #7: sizing Kelly-fraksi adaptif → DITOLAK

Hipotesis: risiko flat 1%/trade tidak optimal. Skala risiko naik saat edge
terkini positif, turun saat rugi beruntun → kurva ekuitas lebih halus → Sharpe
naik. Beda dari scale-out #4C (yg memotong pemenang): Kelly TIDAK memotong
posisi, hanya mengubah UKURAN entry baru — pemenang tetap dibiarkan penuh.

KAUSAL: faktor skala = `clip(1 + kf * expektansi_R_bergulir, min, max)` di mana
expektansi R diambil dari trade yang SUDAH ditutup sebelum entry (exit hari-d
diproses sebelum entry hari-d di engine). `r_multiple` tak tergantung jumlah
lembar → sizing tak mengganggu sinyal edge (tak ada loop liar). Gerbang risiko:
scale di-clamp agar risiko efektif tak pernah > hard cap 2%. `kelly_fraction`
dipilih PER FOLD dari in-sample; diuji OOS-blind.

Hasil OOS agregat (log: `data/experiments/structural_7_kelly_2026-07-22.log`):

| Metrik OOS agregat | baseline (flat) | + kelly-fraksi |
|--------------------|-----------------|----------------|
| Jumlah trade | 190 | 204 |
| Profit factor | **1,38** | 1,32 |
| Expectancy | +0,23 R | +0,14 R |
| Max drawdown | **−11,3%** | −17,9% |
| **Sharpe** | **+0,54** | +0,39 |
| Total return OOS | **+33,9%** | +25,0% |

**Semua metrik OOS memburuk**, MDD paling parah (−11,3% → −17,9% — justru
kebalikan dari tujuan "haluskan ekuitas"). Diagnosis: expektansi R bergulir
ternyata **noisy & mean-reverting**, bukan persisten. Sistem memperbesar posisi
tepat SETELAH streak menang (mean-reversion → drawdown menyusul) dan memperkecil
setelah streak rugi (rebound terlewat). kelly_fraction terpilih dominan 2,0
(5/13 fold) — search konvergen ke nilai agresif yang justru memperbesar
kesalahan timing sizing. Edge harian kita terlalu tipis & episodik untuk
di-"Kelly"-kan.

**Verdikt: TOLAK.** Default tetap `kelly.enabled=False` (flat sizing 1%). Kode &
gerbang risiko dipertahankan (unit-tested — `tests.test_backtest.
test_kelly_sizing_scale_and_neutrality`), bukan bagian strategi default. Gate
live tetap DIBLOKIR benar oleh Sharpe flat 0,54 < 1,0.

### Perbaikan struktural #8: filter lebar pasar (breadth) → DITOLAK

Eksperimen **non-price PERTAMA**. Hipotesis: partisipasi universe (berapa % saham
di atas EMA-nya) = konteks internal pasar yang TIDAK ditangkap regime (indeks bisa
ditopang segelintir raksasa) maupun RS (relatif per-saham). Breadth dihitung dari
OHLCV yang SUDAH ada → kausal, tanpa risiko lookahead. (Fundamental yfinance ditolak
sebagai kandidat: hanya snapshot TERKINI, bukan point-in-time → lookahead parah di
backtest 2016–2026.) Gate ENTRY: blokir posisi baru bila breadth < `min_breadth`;
`min_breadth` dipilih PER FOLD dari in-sample, diuji OOS-blind.

Hasil OOS agregat (log: `data/experiments/structural_8_breadth_2026-07-22.log`):

| Metrik OOS agregat | baseline (tanpa) | + breadth filter |
|--------------------|------------------|------------------|
| # trade            | 190              | 114              |
| Sharpe             | **+0,54**        | +0,09            |
| Profit factor      | 1,38             | 1,12             |
| Expectancy (R)     | +0,23            | +0,09            |
| Return             | +33,9%           | +2,4%            |
| Max drawdown       | −11,3%           | −14,3%           |

Breadth memburuk SEMUA metrik sekaligus: memangkas 76 trade (banyak pemenang ikut
tersaring) tanpa memperbaiki kualitas — Sharpe anjlok, PF & expectancy turun, MDD
malah lebih dalam. Diagnosis: breadth rendah sering bertepatan dengan justru titik
akumulasi terbaik (beli saat pesimis); filter ini membuang entri kontrarian yang
menguntungkan. Regime (tren indeks) + RS (kekuatan relatif) sudah menangkap konteks
"jangan lawan tren" secara lebih tepat; breadth menambah kebisingan, bukan informasi.
`min_breadth` terpilih tersebar (0,4/0,5/0,6) — tidak ada nilai stabil, tanda tak ada
edge nyata. **Verdikt: TOLAK.** Default tetap `breadth.enabled=False`. Kode + unit test
(`test_breadth_filter_blocks_narrow_market`) dipertahankan, bukan strategi default.

**Skor riset struktural sejauh ini** (metodologi jujur, adopsi hanya OOS-winner):

| # | Perbaikan | Verdikt | Sharpe Δ vs sebelumnya |
|---|-----------|---------|------------------------|
| 1 | Regime filter (IHSG > EMA200) | ✅ ADOPSI | −0,31 → +0,20 |
| 2 | Trailing stop (chandelier ATR) | ❌ TOLAK | +0,20 → −0,12 |
| 3 | RS filter (saham > IHSG, lookback per-fold) | ✅ ADOPSI | +0,20 → **+0,54** |
| 4A | Vol filter (ATR% percentile) | ❌ TOLAK | +0,54 → +0,39 |
| 4B | Position weight cap | ❌ TOLAK | +0,54 → +0,51 |
| 4C | Scale-out partial | ❌ TOLAK | +0,54 → +0,34 |
| 5 | Correlation filter antar-posisi | ❌ TOLAK | +0,54 → +0,44 |
| 6 | Timeframe mingguan (W-FRI resample) | ❌ TOLAK | +0,54 → **−0,82** |
| 7 | Sizing Kelly-fraksi adaptif | ❌ TOLAK | +0,54 → +0,39 |
| 8 | Filter lebar pasar (breadth, non-price) | ❌ TOLAK | +0,54 → +0,09 |
| 9 | Kunci param (buang re-optimasi per-fold) | ❌ TOLAK | 0,72 = bias seleksi (default 4/480) |

**Pola yang muncul (9 eksperimen, 2 adopsi):** kedua yang lolos (regime, RS)
adalah **filter ENTRY berbasis tren pasar/relatif** — mereka menambah informasi
BARU (konteks makro & peer). Yang ditolak semuanya adalah **manipulasi
exit/sizing berbasis histori price sendiri** (trailing, scale-out, weight-cap,
korelasi, Kelly) atau **timeframe**. Breadth (#8) menghaluskan pola ini: ia
memang informasi "baru", tapi bertindak sebagai gate ENTRY yang **redundan &
kontrarian-merusak** — regime + RS sudah menangkap "jangan lawan tren" lebih
tepat, sehingga breadth malah membuang entri akumulasi yang menguntungkan.
Pelajaran: bukan sekadar "informasi baru" yang menang, tapi informasi baru yang
**tidak berkorelasi dengan filter yang sudah ada** dan **tidak menyaring justru
saat sinyal terbaik muncul**. Data OOS berulang kali menolak selain regime & RS.

### Audit robustness sistem adopsi (regime + RS) — BUKAN eksperimen fitur

Setelah 8 eksperimen (2 adopsi), sebelum pernah percaya Sharpe OOS 0,54 untuk live,
sistem adopsi di-stress-test (`scripts/audit_robustness.py`, log:
`data/experiments/audit_robustness_2026-07-23.log`). Ini audit, tak mengadopsi/menolak
apa pun. Tiga pemeriksaan:

**1. Fold-by-fold (13 fold, grid jujur penuh, fee 20bps).** Apakah 0,54 ditopang satu
fold beruntung? **Tidak.** 8 fold positif / 5 negatif, tersebar 2019→2026 lintas rezim;
tak ada satu fold yang mendominasi. Terbaik #5 +1,63 (2021-22), terburuk #1 −1,99 (hanya
6 trade). **Tapi** 5/13 fold negatif = edge jelas **bergantung rezim & tidak konsisten**
— justru alasan Sharpe 0,54 < 1,0 dan gate live benar memblokir.

**2. Sensitivitas biaya (param dikunci di nilai adopsi).** Sharpe turun landai 0,77 →
0,60 saat fee 10→40 bps; di **40 bps (dua kali asumsi)** masih +0,60, PF 1,44. Edge
**kokoh terhadap biaya/slippage** dunia nyata.

**3. Sensitivitas jendela IS/OOS (param dikunci).** Sharpe 0,50–0,80 di empat protokol,
tanpa ganti tanda. IS=630 terlemah (0,50, param lebih basi). **Bukan artefak** satu
pilihan protokol.

**Temuan tak terduga (kemudian DIBANTAH — lihat #9):** param yang DIKUNCI di nilai adopsi
(buy=60/atr=3,0/RR=3,0/lb=252) mencetak **0,72 OOS** — lebih tinggi dari **0,54** hasil
re-optimasi grid per-fold. Ini SEMPAT terlihat seperti bukti bahwa grid search per-fold
menambah kebisingan overfit. TAPI angka 0,72 dicurigai bias seleksi (default bisa saja pernah
dipilih dengan melihat seluruh histori), jadi ditandai "perlu uji jujur tersendiri, bukan
adopsi diam-diam". Uji itu = eksperimen #9 di bawah, yang MEMBANTAH temuan ini.

### Perbaikan struktural #9: kunci param (buang re-optimasi per-fold) → DITOLAK (bias seleksi)

Menindaklanjuti temuan audit 0,72 vs 0,54. Hipotesis: mengunci param default (tanpa
re-optimasi grid per-fold) benar-benar lebih baik OOS. Uji jujur (`scripts/experiment_fixed_vs_adaptive.py`,
LQ45, IS 504 / OOS 126, fee 20bps): bukan membandingkan **satu** angka (0,72 vs 0,54) —
itu jebakan — melainkan mengunci **SETIAP** dari 480 kombinasi grid satu per satu dan
menempatkan default di dalam sebaran OOS Sharpe-nya.

| | OOS Sharpe |
|---|---|
| Adaptif (re-optimasi grid per-fold, jujur) | **+0,525** ✓ reproduksi 0,54 |
| Default DIKUNCI (buy=60/atr=3,0/RR=3,0/lb=252) | **+0,718** ✓ reproduksi 0,72 |
| Sebaran 480 kombinasi dikunci | min −0,879 · median **+0,069** · max +0,895 |

**Verdikt: DITOLAK — 0,72 adalah bias seleksi, bukan efek "mengunci lebih baik".** Default
menempati **peringkat 4/480 (persentil 99)** di sebaran param dikunci. Kombinasi dikunci yang
mengalahkan adaptif hanya **13/480 (3%)** — artinya **97% pilihan kunci yang "buta" KALAH** dari
re-optimasi per-fold. Kalau kita mengunci param generik (bukan yang kebetulan jadi default),
rata-rata Sharpe ~0,07. Jadi re-optimasi per-fold BUKAN kebisingan; ia justru mengungguli
hampir semua pilihan kunci buta. Angka 0,72 hanya muncul karena default duduk di puncak
sebaran dengan hindsight. **0,525 tetap estimasi jujur; gate live 1,0 tetap memblokir.** Uji
ini melindungi integritas angka, bukan menaikkannya.

**Kesimpulan audit:** 0,54 itu jujur & kokoh (bukan fluke satu fold, tahan biaya & jendela),
tapi tidak konsisten antar-rezim → gate live 1,0 benar tetap memblokir. Fokus berikutnya =
kumpulkan rekam jejak paper (launchd jalan), bukan berburu fitur (risiko multiple-testing).

### Validasi universe: edge bertahan di luar LQ45 — BUKAN eksperimen fitur

Sebelum dashboard memberi verdikt pada saham di luar LQ45, edge diuji ulang di universe
lebih luas. Universe di-ekspansi dari LQ45 (45 nama) ke **papan Utama IDX + seluruh LQ45**
(277 nama di-ingest; sumber: export daftar saham IDX 2026-07-23). Screen likuiditas produksi
menyisakan **183 nama tradable**. Walk-forward yang SAMA (IS 504 / OOS 126, 13 fold, grid
480 kombinasi, fee 20bps) dijalankan di dua universe:

| Metrik OOS agregat | LQ45 (45) | Papan Utama likuid (183) |
|---|---|---|
| Sharpe | 0,53 | 0,58 |
| Profit factor | 1,37 | 1,46 |
| Expectancy | +0,22 R | +0,22 R |
| Win rate | 40,3% | 39,4% |
| Total return | +32,6% | +36,7% |
| Max drawdown | −11,3% | −11,6% |
| Trade | 191 | 208 |

**Verdikt: edge TIDAK terdegradasi** di universe lebih luas (Sharpe 0,53→0,58, PF 1,37→1,46,
expectancy identik +0,22R, drawdown ~sama). Lebih banyak nama = lebih banyak peluang
(191→208 trade) tanpa mengencerkan kualitas.

**Tiga caveat kejujuran (kenapa ini "tidak degradasi", bukan "lebih baik"):**
1. **Look-ahead seleksi.** Screen likuiditas memakai 20 bar TERAKHIR untuk memilih 183 nama,
   jadi saham yang tak likuid 2019-2023 tapi likuid belakangan ikut terpilih retroaktif →
   hasil ekspansi sedikit optimistis. Kenaikan +0,05 Sharpe ada dalam pita noise itu.
2. **Hanya +17 trade dalam ~7 tahun.** Mayoritas edge tetap di large/mid-cap likuid yang
   beririsan dengan LQ45; 138 nama tambahan menyumbang sedikit.
3. **Gate live tak berubah.** 0,58 < 1,0 → tetap DIBLOKIR dari live. Ini memvalidasi
   *keluasan universe*, bukan *kesiapan trading*.

**Keputusan:** universe diperluas dipakai untuk dashboard & paper (jujur non-degradasi, beri
lebih banyak kandidat), tapi nama non-LQ45 diberi label `extended ⚠` — dihitung dengan gerbang
yang sama, edge OOS-nya belum spesifik divalidasi per-nama; perlakukan sebagai kandidat riset.
Jalur ke live tetap: rekam jejak paper + Sharpe OOS > 1,0 via perbaikan struktural, BUKAN
memperbesar universe.

## Aturan sinyal (watchlist utama)

BUY hanya jika **SEMUA** terpenuhi:
0. **Regime pasar bullish**: IHSG di atas EMA200-nya (tidak melawan tren pasar)
0b. **Kekuatan relatif positif**: saham mengungguli IHSG selama ~252 hari
   (beli pemimpin, bukan pengikut)
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
3. **Konstituen indeks berubah** (IDX evaluasi LQ45 tiap Feb & Agu). Daftar LQ45/IDX30 di
   `universe.py` adalah snapshot manual dan HARUS ditinjau berkala. Universe penuh dimuat
   dari export CSV IDX (`data/universe/idx_tradingview.csv`) via `idx_tickers("IDX")`;
   file itu juga snapshot bertanggal — refresh saat daftar saham IDX berubah.
4. **VWAP** di sini kumulatif atas seluruh data (referensi harian). VWAP intraday
   sejati (reset harian) perlu data intraday di fase eksekusi.

## Belum ada di fase ini

Integrasi broker IDX-live nyata (uang riil) — interface sudah ada tapi jalur
eksekusinya di-gate keras & placeholder-nya menolak sampai gerbang lolos. Modul
US masih pakai universe kosong (`universe.py`) sehingga Alpaca perlu diisi daftar
ticker US dulu sebelum berguna. Arsitektur broker modular agar mudah disambung —
namun aktivasi live tetap menunggu backtest + rekam jejak paper yang lolos jujur.
