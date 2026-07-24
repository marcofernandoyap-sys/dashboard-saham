"""
Universe saham untuk screening.

IDX: konstituen LQ45 / IDX30 (paling likuid) sebagai universe utama.
US : placeholder S&P 500 + Nasdaq 100 (diisi saat fase US).

CATATAN PENTING:
  Konstituen indeks BERUBAH secara berkala (IDX mengevaluasi LQ45 tiap
  Februari & Agustus). Daftar di bawah adalah snapshot manual dan HARUS
  ditinjau/di-update berkala. Jangan anggap ini selalu akurat.

  Idealnya daftar ini di-refresh dari sumber resmi (IDX / RTI) di fase
  ingestion lanjutan; untuk sekarang dipakai sebagai seed universe.
"""
from __future__ import annotations

import csv
from pathlib import Path

# Lokasi default export TradingView (full IDX ~900 nama). Taruh CSV di sini.
DEFAULT_IDX_UNIVERSE_CSV = (
    Path(__file__).resolve().parent.parent / "data" / "universe" / "idx_tradingview.csv"
)

# Snapshot LQ45 (kode tanpa suffix). Tinjau ulang tiap evaluasi indeks.
LQ45 = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "UNVR", "ICBP", "INDF",
    "KLBF", "GGRM", "UNTR", "ADRO", "PGAS", "PTBA", "ANTM", "INCO", "MDKA",
    "ITMG", "SMGR", "INTP", "CPIN", "JPFA", "AKRA", "EXCL", "TOWR", "TBIG",
    "MNCN", "ACES", "MAPI", "ERAA", "BRPT", "TPIA", "BUKA", "GOTO", "MEDC",
    "ARTO", "BRIS", "ISAT", "AMRT", "BBTN", "HRUM", "INKP", "MAPA", "ESSA",
]

# IDX30 adalah subset paling likuid dari LQ45.
IDX30 = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "ICBP", "INDF", "KLBF",
    "UNTR", "ADRO", "PGAS", "PTBA", "ANTM", "INCO", "MDKA", "SMGR", "INTP",
    "CPIN", "AKRA", "TOWR", "ACES", "BRPT", "TPIA", "GOTO", "MEDC", "BRIS",
    "ISAT", "AMRT", "HRUM",
]

# Placeholder US (diisi di fase US).
SP500_SEED: list[str] = []
NASDAQ100_SEED: list[str] = []


def _normalize_idx_symbol(raw: str) -> str | None:
    """
    Normalkan satu simbol mentah dari TradingView jadi 'KODE.JK'.

    Menangani bentuk umum: 'IDX:BBCA', 'BBCA', 'bbca', 'BBCA.JK', ' BBCA '.
    Kembalikan None kalau baris kosong / bukan kode saham yang wajar.
    """
    if not raw:
        return None
    s = raw.strip().upper()
    if not s:
        return None
    # Buang prefix bursa 'IDX:' (atau 'IDX' saja tanpa titik dua) di depan.
    if s.startswith("IDX:"):
        s = s[4:]
    # Buang suffix .JK dulu supaya normalisasi seragam.
    if s.endswith(".JK"):
        s = s[:-3]
    s = s.strip()
    if not s:
        return None
    # Kode IDX = alfanumerik (umumnya 4 huruf). Tolak yang jelas bukan kode.
    if not s.isalnum():
        return None
    return f"{s}.JK"


def load_tradingview_csv(path: str | Path | None = None) -> list[str]:
    """
    Baca export CSV TradingView (full IDX) -> list ticker '.JK' terdedup.

    TradingView mengekspor kolom yang bervariasi; kita deteksi kolom simbol
    secara robust ('Ticker' / 'Symbol' / 'Kode'), fallback ke kolom pertama.
    Urutan asli dipertahankan; duplikat dibuang.
    """
    csv_path = Path(path) if path is not None else DEFAULT_IDX_UNIVERSE_CSV
    if not csv_path.exists():
        raise FileNotFoundError(
            f"CSV universe TradingView tidak ditemukan: {csv_path}. "
            "Ekspor daftar dari TradingView lalu taruh di lokasi itu "
            "(atau berikan --csv)."
        )

    seen: set[str] = set()
    out: list[str] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        symbol_col: str | None = None
        if reader.fieldnames:
            for cand in ("Ticker", "Symbol", "Kode", "ticker", "symbol"):
                if cand in reader.fieldnames:
                    symbol_col = cand
                    break
            if symbol_col is None:
                symbol_col = reader.fieldnames[0]
        if symbol_col is None:
            return out
        for row in reader:
            tk = _normalize_idx_symbol(row.get(symbol_col, ""))
            if tk and tk not in seen:
                seen.add(tk)
                out.append(tk)
    return out


def idx_tickers(index: str = "LQ45", *, csv_path: str | Path | None = None) -> list[str]:
    """
    Kembalikan ticker IDX dengan suffix .JK untuk dipakai yfinance.

    index='IDX' / 'ALL' memuat full universe dari export TradingView
    (lihat DEFAULT_IDX_UNIVERSE_CSV atau argumen csv_path).
    """
    index = index.upper()
    if index in {"IDX", "ALL", "FULL"}:
        return load_tradingview_csv(csv_path)
    base = {"LQ45": LQ45, "IDX30": IDX30}.get(index)
    if base is None:
        raise ValueError(
            f"Index IDX tidak dikenal: {index!r} (pilih LQ45/IDX30/IDX)"
        )
    # dedup sambil menjaga urutan
    seen: set[str] = set()
    out: list[str] = []
    for code in base:
        if code not in seen:
            seen.add(code)
            out.append(f"{code}.JK")
    return out
