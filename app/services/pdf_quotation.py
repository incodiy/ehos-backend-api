"""Penawaran quotation PDF — PRD-F-09 (task 8d).

Generator PDF murni tanpa dependency eksternal (stack G1 locked), reuse
primitif dari `pdf_report.py` (Canvas, `_build_pdf`, sanitasi cp1252).

Isi dokumen:
- **Kop penawaran**: nama hotel (ber-kop), kota, nomor + tgl quotation.
- **Detail acara**: klien (dinas/perusahaan), package, pax, tanggal event.
- **Rincian biaya**: gross, diskon (bila ada), total final.
- **Verifikasi pagu SBM**: snapshot `sbm_rate_value`/`sbm_fiscal_year` (E3) —
  nilai HISTORIS stabil, bukan tarif live (ADR-009/ARD-009).
- **Barcode Code39** dari `quotation_no` — scanable tanpa dependency
  (embedded encoding, iluminasi baris modul tipis/tebal).

Ekspos: `render_quotation_pdf(lead, hotel, quotation)` → bytes PDF.
"""

from __future__ import annotations

from datetime import datetime

from app.models import Lead, Quotation
from app.models.master import Hotel
from app.services.pdf_report import Canvas, _build_pdf, _fmt_date, _safe

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 50

# ─── Code39 (F-09 barcode) ────────────────────────────────────────────────
# 9 elemen (bar/space bergantian, mulai bar); 1 = wide, 0 = narrow.
CODE39 = {
    "0": "000110100", "1": "100100001", "2": "001100001", "3": "101100000",
    "4": "000110001", "5": "100110000", "6": "001110000", "7": "000100101",
    "8": "100100100", "9": "001100100",
    "A": "100001001", "B": "001001001", "C": "101001000", "D": "000011001",
    "E": "100011000", "F": "001011000", "G": "000001101", "H": "100001100",
    "I": "001001100", "J": "000011100",
    "K": "100000011", "L": "001000011", "M": "101000010", "N": "000010011",
    "O": "100010010", "P": "001010010", "Q": "000000111", "R": "100000110",
    "S": "001000110", "T": "000010110",
    "U": "110000001", "V": "011000001", "W": "111000000", "X": "010010001",
    "Y": "110010000", "Z": "011010000",
    "-": "010000101", ".": "110000100", " ": "011000100",
    "$": "010101000", "/": "010100010", "+": "010001010", "%": "000101010",
    "*": "010010100",
}

NARROW = 1.2
WIDE = 2.4
GAP = NARROW
QUIET = 12
BAR_H = 42


def _rupiah(value: float | None) -> str:
    if value is None:
        return "-"
    return f"Rp{value:,.0f}"


def _code39_bits(code: str) -> list[str]:
    """Symbol sequence: '*data*' → list of wide/narrow bitmaps."""
    allowed = set(CODE39)
    data = code.upper()
    bad = [ch for ch in data if ch not in allowed]
    if bad:
        raise ValueError(f"quotation_no mengandung karakter di luar Code39: {set(bad)}")
    return [CODE39["*"]] + [CODE39[ch] for ch in data] + [CODE39["*"]]


def _draw_barcode(cv: Canvas, code: str) -> None:
    """Gambar barcode Code39 di posisi kursor saat ini (baris horizontal)."""
    y = cv._y - 8
    x = MARGIN + QUIET
    patterns = _code39_bits(code)
    for bits in patterns:
        x_cursor = x
        for idx, wide in enumerate(bits):
            w = WIDE if wide == "1" else NARROW
            if idx % 2 == 0:  # bar (hitam)
                cv._ops.append(f"0 0 0 rg {x_cursor:.1f} {y:.1f} {w:.1f} {BAR_H:.1f} re f 0 g")
            x_cursor += w
        x = x_cursor + GAP
    cv._y = y - BAR_H - 10
    cv.text(code, size=7.5, x=MARGIN + QUIET)
    cv._y -= 6


def render_quotation_pdf(lead: Lead, hotel: Hotel, quotation: Quotation, province_name: str | None = None) -> bytes:
    """Susun penawaran quotation ber-kop + tabel harga + barcode Code39."""
    cv = Canvas(f"{_safe(hotel.name)} \u2014 Penawaran MICE", page_label="Halaman")
    cv._ops = [cv._header()]

    # ── kop penawaran ────────────────────────────────────────────────────
    cv.text(hotel.name, bold=True, size=15)
    cv.gap(2)
    cv.text(f"{hotel.city or '-'}, {province_name or '-'}  |  Kode Hotel: {hotel.code}", size=9)
    cv.hline()
    cv.gap(14)

    cv.text("SURAT PENAWARAN HARGA", bold=True, size=12)
    cv.gap(12)
    cv.text(f"No. Quotation: {quotation.quotation_no}", size=9)
    cv.text(f"Tanggal: {_fmt_date(quotation.created_at or datetime.now())}", size=9)
    discount_label = (
        "PENDING APPROVAL GM" if quotation.discount_approval_status == "PENDING" else "APPROVED"
    )
    cv.text(
        f"Status: {quotation.status}   Diskon: {discount_label}",
        size=9,
    )
    cv.hline()
    cv.gap(14)

    # ── data acara ───────────────────────────────────────────────────────
    cv.text("Kepada Yth.", size=9)
    cv.text(lead.company_name, bold=True, size=10)
    cv.text(f"PIC: {lead.pic_name or '-'}  |  {lead.pic_email or lead.pic_phone or ''}", size=9)
    cv.gap(12)
    cv.text("Rincian Acara", bold=True, size=11)
    cv.gap(10)
    cv.text(f"Acara   : {quotation.event_name or '-'}", size=9)
    cv.text(f"Tanggal : {_fmt_date(quotation.event_date) if quotation.event_date else '-'}", size=9)
    cv.text(f"Paket   : {quotation.package_type}", size=9)
    cv.text(f"Pax     : {quotation.pax_count} peserta", size=9)
    cv.gap(10)

    # ── rincian biaya ────────────────────────────────────────────────────
    cv.shade(0.92, 96)
    cv.text("Rincian Biaya", bold=True, size=11)
    cv.gap(14)
    cv.text(f"Gross         : {_rupiah(quotation.gross_amount)}", size=9.5)
    cv.gap(11)
    cv.text(f"Diskon        : {_rupiah(quotation.discount_amount)}", size=9.5)
    cv.gap(11)
    cv.text(f"TOTAL AKHIR   : {_rupiah(quotation.final_amount)}", bold=True, size=10.5)
    cv.gap(13)
    cv.text(
        f"Rata-rata /pax: "
        f"{_rupiah(quotation.final_amount / quotation.pax_count if quotation.pax_count else None)}",
        size=8.5,
    )
    cv.gap(14)

    # ── verifikasi pagu SBM (snapshot E3) ────────────────────────────────
    cv.text("Verifikasi Pagu SBM (Kemenkeu)", bold=True, size=10)
    if quotation.sbm_rate_value is not None:
        cv.text(
            f"Rating /pax maks {_rupiah(quotation.sbm_rate_value)} (Paket {quotation.package_type}, "
            f"TA {quotation.sbm_fiscal_year}) \u2014 snapshot stabil saat generate.",
            size=8.5,
        )
        effective = (
            quotation.final_amount / quotation.pax_count
            if quotation.pax_count and quotation.pax_count > 0
            else None
        )
        pagu = (
            "dalam pagu"
            if effective and effective <= quotation.sbm_rate_value
            else "di luar pagu (membutuhkan persetujuan)"
        )
        cv.text(
            f"Rate efektif /pax: {_rupiah(effective)} \u2014 {pagu}",
            size=8.5,
        )
    else:
        cv.text("Snapshot SBM tidak tersedia (penawaran non-pemerintah/PRIVATE).", size=8.5)
    cv.hline()
    cv.gap(12)

    # ── barcode ──────────────────────────────────────────────────────────
    cv.text("Scan untuk verifikasi nomor quotation (Code39):", size=8)
    cv.gap(4)
    _draw_barcode(cv, quotation.quotation_no)
    cv.gap(8)
    cv.text(
        "Dokumen ini disusun otomatis oleh EHOS \u2014 verifikasi pagu SBM Kemenkeu (F-09).",
        size=7,
    )

    return _build_pdf(cv.finalize())