"""
Layer eksekusi order (Fase 5b paper / Fase 6 live).

PRINSIP RISK-FIRST (non-negotiable):
  - Default 'paper' — tidak ada uang riil sampai gerbang lolos & di-set eksplisit.
  - Order LIVE (uang riil) DITOLAK selama:
      * backtest belum lolos gerbang (registry.live_readiness), atau
      * rekam jejak paper-trading belum cukup (min hari & min trade), atau
      * execution.allow_live masih False.
    Gerbang ini TIDAK boleh di-bypass demi mengejar return.

Struktur modular (broker pluggable):
  broker.py  : kontrak abstrak (Order/Fill/Position/Account + Broker ABC).
  paper.py   : PaperBroker lokal (simulasi, zero-dep) — jalan untuk IDX sekarang.
  alpaca.py  : AlpacaBroker (US, paper by default) via urllib.
  journal.py : catat fill & hitung rekam jejak paper (memberi makan gerbang).
  engine.py  : ExecutionEngine — menegakkan gerbang SEBELUM meneruskan ke broker.
"""
