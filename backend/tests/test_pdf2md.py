"""Test CHUYỂN PDF -> MARKDOWN cho kho quy định.

PDF có lớp chữ được DỰNG TẠI CHỖ bằng fpdf2 (đã có trong requirements để xuất báo cáo),
không kèm tệp mẫu vào cây dự án. Font lõi của fpdf2 chỉ có Latin-1 nên văn bản mẫu viết
không dấu — bộ nhận diện "Dieu / Chuong" chấp nhận cả hai dạng.
"""
from __future__ import annotations

import pytest

from app.domain.regulations import pdf2md
from app.domain.regulations.ingest import chunk_markdown


def _pdf(pages: list[list[str]]) -> bytes:
    from fpdf import FPDF

    doc = FPDF()
    doc.set_font("Helvetica", size=11)
    for lines in pages:
        doc.add_page()
        for line in lines:
            doc.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())


_PAGE1 = [
    "Chuong I",
    "Dieu 1. Pham vi dieu chinh va doi tuong",
    "ap dung",
    "Quy dinh nay ap dung cho hop dong dich vu duoc ky ket giua",
    "cac ben trong nuoc va nuoc ngoai.",
    "1. Gia tri hop dong phai ghi ro bang so va bang chu.",
    "2. Thoi han hop dong phai xac dinh ro ngay bat dau va",
    "ngay ket thuc cua hop dong.",
    "a) Thoi han toi da la ba muoi sau thang;",
    "b) Gia han theo thoa thuan cua cac ben.",
    "- 1 -",
]
_PAGE2 = [
    "Dieu 2. Hieu luc thi hanh",
    "Quy dinh nay co hieu luc tu ngay ky va ap dung cho moi hop dong moi.",
    "Cac hop dong da ky truoc ngay nay tiep tuc thuc hien theo noi dung da ky.",
    "2",
]


def test_lop_chu_thanh_tieu_de_va_doan():
    md, st = pdf2md.pdf_to_markdown(_pdf([_PAGE1, _PAGE2]), "Quy dinh mau")
    assert st == {"pages": 2, "from_text": 2, "ocred": 0, "skipped": []}
    lines = md.splitlines()
    assert lines[0] == "# Quy dinh mau"
    assert "## Chuong I" in lines
    # Tên Điều xuống dòng được nối lại; dòng số trang bị bỏ.
    assert "### Dieu 1. Pham vi dieu chinh va doi tuong ap dung" in lines
    assert "### Dieu 2. Hieu luc thi hanh" in lines
    assert "- 1 -" not in md and "\n2\n" not in md
    # Dòng hiển thị liền nhau nối thành đoạn; khoản / điểm mở đoạn mới.
    assert ("Quy dinh nay ap dung cho hop dong dich vu duoc ky ket giua cac ben trong nuoc "
            "va nuoc ngoai.") in lines
    assert ("2. Thoi han hop dong phai xac dinh ro ngay bat dau va ngay ket thuc cua hop "
            "dong.") in lines
    assert "a) Thoi han toi da la ba muoi sau thang;" in lines
    # Bộ cắt đoạn của kho đọc được kết quả (có Điều -> có đoạn).
    assert chunk_markdown(md)


def test_pdf_quet_khong_ocr_bao_loi_ro():
    # Trang trắng (không lớp chữ) ~ bản quét: không bật OCR thì báo rõ, không trả .md rỗng.
    with pytest.raises(ValueError, match="bản chụp"):
        pdf2md.pdf_to_markdown(_pdf([[]]))


def test_trang_quet_xen_ke_duoc_bao_bo_qua():
    md, st = pdf2md.pdf_to_markdown(_pdf([_PAGE1, []]))
    assert st["skipped"] == [2] and st["from_text"] == 1
    assert "Dieu 1." in md


def test_tep_hong():
    with pytest.raises(ValueError):
        pdf2md.pdf_to_markdown(b"khong phai pdf")


def test_lines_to_markdown_giu_dieu_ket_thuc_cau():
    md = pdf2md.lines_to_markdown(["Dieu 3. Xu ly vi pham.", "nguoi vi pham bi xu phat."])
    # Tiêu đề đã kết thúc câu -> dòng sau là đoạn, không bị nối vào tiêu đề.
    assert md.splitlines()[0] == "### Dieu 3. Xu ly vi pham."
    assert "nguoi vi pham bi xu phat." in md.splitlines()


def test_cli_ghi_canh_tep(tmp_path, monkeypatch):
    src = tmp_path / "quy_dinh.pdf"
    src.write_bytes(_pdf([_PAGE1]))
    monkeypatch.setenv("INIT_CWD", str(tmp_path))
    assert pdf2md.main(["quy_dinh.pdf"]) == 0
    assert (tmp_path / "quy_dinh.md").read_text(encoding="utf-8").startswith("# quy dinh")
    assert pdf2md.main(["khac.txt"]) == 1


def test_ocr_trang_quet_nho_dem_theo_trang(tmp_path, monkeypatch):
    """Lượt nạp bản quét bị ngắt rồi chạy lại -> trang đã đọc lấy từ bộ nhớ, không đọc ảnh lại."""
    from app.domain.documents import ocr

    read: list[int] = []
    monkeypatch.setattr(ocr, "render_pages", lambda data, idx: ((i, f"img{i}") for i in idx))

    def _ocr(img):
        read.append(int(img[3:]))
        return [{"text": f"Dieu {img[3:]}. Noi dung trang quet so {img[3:]} day du."}]

    monkeypatch.setattr(ocr, "ocr_image_lines", _ocr)
    data = _pdf([[], [], []])
    seen: list[tuple[int, int]] = []
    md, st = pdf2md.pdf_to_markdown(data, ocr=True, cache_dir=tmp_path,
                                    on_page=lambda d, n: seen.append((d, n)))
    assert read == [0, 1, 2] and st["ocred"] == 3 and seen[-1] == (3, 3)
    (tmp_path / "2.json").unlink()            # giả lập: bị ngắt trước khi xong trang 3
    read.clear()
    md2, _ = pdf2md.pdf_to_markdown(data, ocr=True, cache_dir=tmp_path)
    assert read == [2] and md2 == md
