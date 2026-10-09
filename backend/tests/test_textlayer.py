"""Test ĐỌC LỚP VĂN BẢN — chấm điểm rác, dựng dòng có tọa độ, chia việc với OCR.

Đây là tầng quyết định "trang này khỏi OCR", nên sai ở đây tốn theo hai hướng ngược
nhau: tin nhầm một trang rác thì trích ra giá trị sai, còn nghi oan một trang sạch thì
mất 45 giây mỗi trang. Cả hai hướng đều có ca kiểm ở dưới.

PDF dùng để kiểm được DỰNG TẠI CHỖ bằng fpdf2 (đã có trong requirements), không kèm
tệp mẫu vào cây dự án.
"""
from __future__ import annotations

import pytest

from app.core import settings
from app.domain.documents import textlayer

# Đoạn OCR hỏng theo đúng kiểu lớp văn bản mà công cụ OCR của bên thứ ba nhúng sẵn
# vào PDF scan: dấu chấm/phẩy chen giữa chữ, chữ hoa mọc giữa từ.
RAC = ("SUPPI.IiR shall i,nnrediatcly causc thc rcp.air and rcplaocnrent of the "
       "DIiV,^CE at the SUPPI.IiR'S sole exfcnsc. (]IITiW N'T,\\NNTN(i (iRFIiN4I:NT No "
       "04/(bnliacl 2i)24 oar SIiRV M NNIN(i r\\qtrilNlliN I APPTiNDIX 3 -GOODS "
       "ANt) SIiRV lNQUrRy thc rcp.air i,nnrediatcly causc")

SACH_VI = ("CỘNG HOÀ XÃ HỘI CHỦ NGHĨA VIỆT NAM Độc lập Tự do Hạnh Phúc "
           "HỢP ĐỒNG DỊCH VỤ số 15/2025/HĐDV giữa công ty cổ phần thương mại "
           "Minh Phát và công ty trách nhiệm hữu hạn dịch vụ An Bình "
           "Kính gửi phòng hành chính tổng hợp của hai bên ký kết")

SACH_EN = ("Damage which is not as a result of normal use of the goods is not "
           "covered by the Agreement. Any payment effected under the clauses above "
           "shall be without prejudice to any claim for compensation made in law, "
           "but shall be deducted from any settlement in respect of such claims.")


# ---------------------------------------------------------------------------
# Chấm điểm rác
# ---------------------------------------------------------------------------
def test_trang_sach_diem_thap_trang_rac_diem_cao():
    assert textlayer.junk_ratio(SACH_VI) < 0.06
    assert textlayer.junk_ratio(SACH_EN) < 0.06
    assert textlayer.junk_ratio(RAC) > 0.06


def test_chu_hoa_tieng_viet_khong_bi_cham_la_rac():
    """Khoảng mã `à-ỹ` nuốt luôn `Đ` (U+0110) và `Ộ` (U+1ED8), nên một phép chấm dựa
    trên khoảng mã sẽ coi MỌI trang tiếng Việt viết hoa là rác."""
    hoa = "BỘ LAO ĐỘNG THƯƠNG BINH VÀ XÃ HỘI " * 4
    assert textlayer.junk_ratio(hoa) == 0.0


def test_qua_it_tu_latin_thi_khong_ket_luan():
    """Trang tiếng Nhật: phép chấm chỉ bắt lỗi nhận dạng chữ LATIN nên nó không có ý
    kiến, và 'không có bằng chứng hỏng' khác hẳn 'có bằng chứng hỏng'."""
    nhat = "第 5 項 上記第 1項乃至第 4項に基づいて行われた支払いは、法律の下に成される " * 3
    assert textlayer.junk_ratio(nhat) is None
    assert textlayer.junk_ratio("") is None
    assert textlayer.junk_ratio("chỉ vài từ thôi") is None


# ---------------------------------------------------------------------------
# Quyết định tin hay không
# ---------------------------------------------------------------------------
def _page(chars: int, junk: float | None) -> dict:
    return {"index": 0, "lines": [], "chars": chars, "junk": junk, "meta": {}}


@pytest.mark.parametrize(("chars", "junk", "mong_doi"), [
    (2000, 0.01, True),      # trang sạch, đủ chữ
    (2000, 0.20, False),     # đủ chữ nhưng là bản OCR hỏng nhúng sẵn
    (2000, None, True),      # chữ Nhật/Trung — không đủ căn cứ để nghi
    (10, 0.0, False),        # trang ảnh chỉ dính vài chữ tiêu đề
    (2000, 0.06, True),      # đúng ngưỡng vẫn nhận
])
def test_dieu_kien_tin_lop_van_ban(chars, junk, mong_doi):
    assert textlayer.trusted(_page(chars, junk)) is mong_doi


def test_tat_cau_hinh_thi_khong_tin_trang_nao(monkeypatch):
    monkeypatch.setattr(settings, "ocr_text_layer", False)
    assert textlayer.trusted(_page(5000, 0.0)) is False
    assert textlayer.plan(b"%PDF-") == {"pages": [], "from_text": [], "need_ocr": [],
                                        "total": 0}


# ---------------------------------------------------------------------------
# Rút dòng có tọa độ từ PDF thật
# ---------------------------------------------------------------------------
def _font_tieng_viet() -> str:
    """Đường dẫn font TTF có dấu tiếng Việt trên MÁY ĐANG CHẠY TEST.

    Dùng lại `export._pdf_fonts()` — nơi DUY NHẤT của dự án biết tìm font hệ thống ở
    đâu — thay vì viết cứng một đường dẫn. Bản cũ ghi thẳng đường dẫn DejaVu của Linux
    nên bốn ca này đỏ trên Windows, mà lỗi lại là `FileNotFoundError` của fpdf2: đọc lên
    trông như lỗi mã sản phẩm chứ không phải lỗi môi trường test.

    Không có font nào thì BỎ QUA, không phải thất bại: máy thiếu font là chuyện môi
    trường, y như cách `export_pdf` trả 501 thay vì 500 trong cùng tình huống."""
    from app.routers.export import _pdf_fonts

    fonts = _pdf_fonts()
    if not fonts:
        pytest.skip("Máy không có font TTF nào đủ dấu tiếng Việt để dựng PDF kiểm thử.")
    return fonts[0]


def _pdf(dong: list[tuple[float, float, str]]) -> bytes:
    """Dựng PDF một trang: mỗi phần tử là (x mm, y mm, nội dung)."""
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.add_font("VN", "", _font_tieng_viet())
    pdf.set_font("VN", "", 12)
    for x, y, text in dong:
        pdf.set_xy(x, y)
        pdf.cell(0, 6, text)
    return bytes(pdf.output())


def test_rut_dong_giu_dung_chu_va_thu_tu():
    data = _pdf([(20, 20, "Tiền lương: 184.461 JPY/tháng"),
                 (20, 40, "Địa điểm làm việc: Nagoya")])
    pages = textlayer.read_pages(data)
    assert len(pages) == 1
    texts = [ln["text"] for ln in pages[0]["lines"]]
    assert "Tiền lương: 184.461 JPY/tháng" in texts, texts
    assert "Địa điểm làm việc: Nagoya" in texts, texts

    # Dòng cùng dạng với đường đọc ảnh: {text, conf=1.0}, dòng trên đứng trước.
    ln = pages[0]["lines"][0]
    assert set(ln) == {"text", "conf"} and ln["conf"] == 1.0
    assert texts.index("Tiền lương: 184.461 JPY/tháng") < texts.index("Địa điểm làm việc: Nagoya")
    assert pages[0]["meta"]["w"] > 1000 and pages[0]["meta"]["rotate_k"] == 0


def test_hai_cot_khong_bi_dinh_vao_mot_dong():
    """Biểu mẫu hai cột gộp làm một dòng sẽ ra 'THƯƠNG BINH VÀCỘNG HOÀ XÃ HỘI' — nhãn
    và giá trị của hai trường khác nhau dính liền, tầng trích xuất đọc sai cả hai."""
    data = _pdf([(15, 20, "BỘ LAO ĐỘNG"), (120, 20, "CỘNG HOÀ XÃ HỘI")])
    texts = [ln["text"] for ln in textlayer.read_pages(data)[0]["lines"]]
    assert "BỘ LAO ĐỘNG" in texts and "CỘNG HOÀ XÃ HỘI" in texts, texts


def test_pdf_khong_co_chu_thi_khong_co_dong_nao():
    pages = textlayer.read_pages(_pdf([]))
    assert pages[0]["lines"] == [] and pages[0]["chars"] == 0
    assert textlayer.trusted(pages[0]) is False


def test_pdf_hong_khong_lam_sap_pipeline():
    """Đọc lớp văn bản là bước TĂNG TỐC; hỏng thì phải rơi về OCR, không được ném ra."""
    with pytest.raises(Exception):  # noqa: B017 — pypdfium2 ném kiểu riêng của nó
        textlayer.read_pages(b"khong phai pdf")


def test_plan_chia_dung_hai_nhom():
    data = _pdf([(15, 10 + i * 8, f"Dòng nội dung số {i} của hợp đồng dịch vụ mẫu")
                 for i in range(12)])
    plan = textlayer.plan(data)
    assert plan["total"] == 1
    assert plan["from_text"] == [0] and plan["need_ocr"] == []
