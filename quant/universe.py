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


def idx_tickers(index: str = "LQ45") -> list[str]:
    """Kembalikan ticker IDX dengan suffix .JK untuk dipakai yfinance."""
    index = index.upper()
    base = {"LQ45": LQ45, "IDX30": IDX30}.get(index)
    if base is None:
        raise ValueError(f"Index IDX tidak dikenal: {index!r} (pilih LQ45/IDX30)")
    # dedup sambil menjaga urutan
    seen: set[str] = set()
    out: list[str] = []
    for code in base:
        if code not in seen:
            seen.add(code)
            out.append(f"{code}.JK")
    return out
