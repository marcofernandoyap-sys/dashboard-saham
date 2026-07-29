# Menjalankan `paper_daily` otomatis via launchd (macOS)

Automasi paper trading harian supaya rekam jejak (60 hari / 30 trade) mulai
akrit tanpa perlu ingat menjalankan skrip manual.

## Install (satu kali)

```sh
# 1. Salin plist ke folder LaunchAgent (per-user)
cp scripts/launchd/com.marco.quant-paper.plist \
   ~/Library/LaunchAgents/com.marco.quant-paper.plist

# 2. Load ke launchd
launchctl load ~/Library/LaunchAgents/com.marco.quant-paper.plist

# 3. Pastikan terpasang
launchctl list | grep com.marco.quant-paper
```

## Cek jadwal & status

```sh
# Rincian job
launchctl print gui/$(id -u)/com.marco.quant-paper | head -40

# Isi log terakhir
tail -f data/paper/launchd.out.log
tail -f data/paper/YYYY-MM-DD.log   # log per-hari dari _Tee
```

## Jalankan manual sekali untuk uji

```sh
launchctl start com.marco.quant-paper
# lalu cek data/paper/<hari-ini>.log
```

## Update setelah edit plist

```sh
launchctl unload ~/Library/LaunchAgents/com.marco.quant-paper.plist
cp scripts/launchd/com.marco.quant-paper.plist \
   ~/Library/LaunchAgents/com.marco.quant-paper.plist
launchctl load ~/Library/LaunchAgents/com.marco.quant-paper.plist
```

## Uninstall

```sh
launchctl unload ~/Library/LaunchAgents/com.marco.quant-paper.plist
rm ~/Library/LaunchAgents/com.marco.quant-paper.plist
```

## Bangun otomatis saat Mac tidur (pmset) — WAJIB untuk eksekusi harian

launchd hanya menembak saat Mac MENYALA. Supaya `paper_daily` tetap jalan meski
laptop tidur, jadwalkan bangun 1 menit sebelum job (butuh `sudo`, sekali saja):

```sh
sudo pmset repeat wakeorpoweron MTWRF 17:14:00
pmset -g sched          # verifikasi: harus muncul "repeat wakeorpoweron MTWRF 17:14:00"
```

Kenapa 17:14 (bukan zona lain): timezone SISTEM = Asia/Jakarta (WIB), dan launchd
`StartCalendarInterval` memakai waktu lokal SISTEM (env `TZ` di plist hanya
memengaruhi jam yang DICETAK skrip, bukan jadwal launchd). Jadi wake 17:14 WIB +
job 17:15 WIB sejajar; 1 menit = jeda bangun & settle.

Batas jujur (jangan berasumsi 100%):
- **Wake dari sleep**: andal saat AC; umumnya jalan juga di baterai.
- **Mac dimatikan total**: power-on terjadwal TIDAK andal di laptop (sering hanya
  desktop/AC). Backstop: launchd tetap menjalankan job yang terlewat pada wake/boot
  berikutnya.
- **Clamshell + baterai**: deep sleep sesekali bisa lewat satu wake; backstop
  launchd menutupinya di wake berikutnya.
- `pmset repeat` hanya punya SATU slot → perintah ini menggantikan repeat lama
  (bukan one-off system alarm). Hapus dengan `sudo pmset repeat cancel`.

## Dashboard Streamlit otomatis (com.marco.quant-dashboard)

Dashboard read-only bisa dijalankan sebagai LAYANAN TETAP (start saat login,
restart otomatis jika mati) — beda dengan `quant-paper` yang terjadwal harian.

```sh
# Install (satu kali)
cp scripts/launchd/com.marco.quant-dashboard.plist \
   ~/Library/LaunchAgents/com.marco.quant-dashboard.plist
launchctl load ~/Library/LaunchAgents/com.marco.quant-dashboard.plist
launchctl list | grep com.marco.quant-dashboard   # verifikasi

# Akses
open http://localhost:8501
```

- `RunAtLoad=true` + `KeepAlive=true` → dashboard hidup saat login & bangkit lagi
  jika crash. Tidak ada `StartCalendarInterval` (bukan job terjadwal).
- **Tidak perlu restart harian**: dashboard punya fragment auto-refresh yang cek
  kesegaran data tiap 30 menit dan reload sendiri saat data harian baru masuk.
- Update setelah edit plist / uninstall: sama seperti `quant-paper` di atas,
  ganti label jadi `com.marco.quant-dashboard`.

## Catatan penting

- **Mac harus menyala** pada jam 17:15 WIB. Kalau Mac tidur, launchd akan
  menjalankan job pada bangun berikutnya (tidak berkumpul, hanya sekali). Untuk
  bangun otomatis, lihat bagian "Bangun otomatis (pmset)" di atas.
- IDX libur nasional tidak dicek oleh skrip — `paper_daily` hanya melewati
  Sabtu/Minggu. Log hari libur nasional akan sekadar "tidak ada sinyal".
- Kredensial broker/notifikasi (kalau ada) dibaca dari `.env` di working
  directory, bukan environment plist.
