"""Test VÙNG CẦN KIỂM TRA: người dùng khoanh vùng hình chữ nhật trên trang và chọn trang quét.

Kiểm ba tầng: phân giải dữ liệu gửi lên (`parse_regions`), lớp chữ PDF chỉ lấy chữ trong
vùng (`textlayer`), và đường đọc ảnh chỉ đọc trang được chọn, ảnh đã cắt theo vùng
(`pipeline._read_document`, OCR giả).
"""
from __future__ import annotations

import pytest
from PIL import Image

from app.core import settings
from app.domain.documents import pipeline, textlayer
from app.domain.documents.intake import SelectionError, parse_regions


def _pdf(pages: list[list[tuple[float, str]]]) -> bytes:
    """Mỗi trang: [(tọa độ y theo mm, dòng chữ)] — đặt chữ ở vị trí biết trước."""
    from fpdf import FPDF

    doc = FPDF()                          # A4 210 x 297 mm
    doc.set_font("Helvetica", size=11)
    for lines in pages:
        doc.add_page()
        for y, text in lines:
            doc.set_xy(20, y)
            doc.multi_cell(170, 7, text)
    return bytes(doc.output())


_TOP = "Phan tren trang gom nhieu chu de du so ky tu toi thieu cho lop van ban. " * 4
_BOT = "Phan duoi trang chua noi dung can kiem tra rieng biet voi phan tren nhe. " * 4


# ── parse_regions ─────────────────────────────────────────────────────────────
def test_parse_regions_chuan_hoa():
    out = parse_regions('[{"skip": [3, 1, 1], "rects": {"0": [0.9, 0.8, 0.1, 0.2], '
                        '"2": [0.1, 0.1, 0.105, 0.5], "4": [-1, 0, 2, 0.5]}}]', 2)
    assert out == [{"skip": [1, 3], "rects": {0: (0.1, 0.2, 0.9, 0.8), 4: (0.0, 0.0, 1.0, 0.5)}},
                   None]                  # vùng hẹp < 1% bị bỏ; vùng tràn bị kẹp
    assert parse_regions("", 2) == [None, None]
    assert parse_regions('[{"skip": [], "rects": {}}]', 1) == [None]


@pytest.mark.parametrize("raw", ['{"a": 1}', "[1, 2, 3]", '[{"rects": {"x": [0, 0, 1, 1]}}]',
                                 '[{"skip": [-1]}]', "khong phai json", "[5]"])
def test_parse_regions_sai_dang(raw):
    with pytest.raises(SelectionError):
        parse_regions(raw, 2)


# ── cắt ảnh ───────────────────────────────────────────────────────────────────
def test_crop_image():
    img = Image.new("RGB", (1000, 2000))
    assert pipeline.crop_image(img, (0.1, 0.5, 0.6, 1.0)).size == (500, 1000)
    assert pipeline.crop_image(img, None) is img
    assert pipeline.crop_image(img, (0.5, 0.5, 0.5001, 0.5001)) is img   # vùng suy biến


# ── lớp chữ theo vùng ─────────────────────────────────────────────────────────
def test_lop_chu_chi_lay_chu_trong_vung():
    data = _pdf([[(30, _TOP), (220, _BOT)]])
    full = " ".join(ln["text"] for ln in textlayer.read_pages(data)[0]["lines"])
    assert "Phan tren" in full and "Phan duoi" in full
    # Nửa dưới trang (gốc tọa độ ở góc TRÊN-trái như ảnh trang).
    lower = textlayer.read_pages(data, {0: (0.0, 0.5, 1.0, 1.0)})[0]
    text = " ".join(ln["text"] for ln in lower["lines"])
    assert "Phan duoi" in text and "Phan tren" not in text


# ── đường đọc với vùng ────────────────────────────────────────────────────────
@pytest.fixture()
def fake_ocr(monkeypatch):
    """OCR giả: ghi lại kích thước ảnh được đọc, trả một dòng chữ."""
    seen: list[tuple[int, int]] = []

    def _ocr(img, with_meta=False):
        seen.append(img.size)
        lines = [{"text": f"anh {img.size[0]}x{img.size[1]}", "conf": 0.9}]
        return (lines, {"h": img.size[1], "w": img.size[0]}) if with_meta else lines

    monkeypatch.setattr(pipeline, "ocr_image_lines", _ocr)
    monkeypatch.setattr(settings, "ocr_text_layer_verify", False)
    return seen


def test_pdf_quet_chi_doc_trang_duoc_chon_va_cat_vung(fake_ocr, monkeypatch):
    monkeypatch.setattr(settings, "ocr_dpi", 72)
    data = _pdf([[], [], []])             # 3 trang trắng ~ bản quét (không lớp chữ)
    region = {"skip": [1], "rects": {2: (0.0, 0.5, 0.5, 1.0)}}
    out = pipeline._read_document(data, {}, "a.pdf", region=region)
    assert out["stats"]["num_pages"] == 2                    # bỏ trang 2
    full, half = fake_ocr
    assert half == (round(full[0] / 2), round(full[1] / 2))   # trang 3 cắt nửa dưới-trái
    assert out["stats"]["region"] == {"pages_skipped": [1], "pages_cropped": [2]}


def test_pdf_co_lop_chu_bo_trang_va_loc_vung(fake_ocr):
    data = _pdf([[(30, _TOP), (220, _BOT)], [(30, _TOP)]])
    out = pipeline._read_document(data, {}, "a.pdf",
                                  region={"skip": [1], "rects": {0: (0.0, 0.5, 1.0, 1.0)}})
    assert "Phan duoi" in out["full_text"] and "Phan tren" not in out["full_text"]
    assert out["stats"]["num_pages"] == 1 and not fake_ocr


def test_bo_het_trang_thi_van_doc_ca_tep(fake_ocr):
    data = _pdf([[(30, _TOP)]])
    out = pipeline._read_document(data, {}, "a.pdf", region={"skip": [0], "rects": {}})
    assert "Phan tren" in out["full_text"]
