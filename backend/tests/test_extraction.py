"""Test trích xuất thuần: chuẩn hóa text, làm sạch giá trị, trích bằng luật theo bộ
trường (nhãn · kiểu giá trị · value_regex), chống-bịa khi merge LLM, điều phối 1 file."""
import asyncio
import re

import pytest

from app.domain.documents.enrich import merge_llm_extraction, money_keys, typed_keys
from app.domain.documents.ocr import fold_diacritics
from app.domain.documents.rules import (
    _condense_clause,
    _find_labeled_text,
    _is_junk_text_value,
    _ocr_tolerant_label_regex,
    _try_parse_date_any,
    clean_value,
    extract_contract_json,
    is_boilerplate_clause,
    normalize_signed_date,
    normalize_text,
)
from app.store import load_field_set

# Hợp đồng mẫu dùng chung — khớp bộ trường mặc định `hop_dong_mau`.
_HD_DICH_VU = (
    "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n"
    "HỢP ĐỒNG DỊCH VỤ\n"
    "Số: 15/2025/HĐDV\n"
    "Hôm nay, ngày 05 tháng 03 năm 2025, tại Hà Nội, chúng tôi gồm:\n"
    "- Bên A: Công ty TNHH Thương mại Minh Phát\n"
    "- Bên B: Công ty Cổ phần Dịch vụ Kỹ thuật An Bình\n"
    "\n"
    "Điều 1. Nội dung dịch vụ\n"
    "- Số lượng: 12 bộ thiết bị\n"
    "- Giá trị hợp đồng: 120.000.000 VND (Một trăm hai mươi triệu đồng)\n"
    "- Thời hạn: 12 tháng kể từ ngày ký\n"
    "- Phương thức thanh toán: Chuyển khoản, chia làm 2 đợt\n"
    "- Giải quyết tranh chấp: Tòa án nhân dân có thẩm quyền tại Hà Nội\n"
)


@pytest.fixture(scope="module")
def mau() -> dict:
    return load_field_set("hop_dong_mau")


def _extract(text: str, field_set: dict) -> dict:
    t = normalize_text(text)
    return extract_contract_json("s1", "hop dong.pdf", t, t, field_set)[0]


def _values(text: str, field_set: dict) -> dict:
    return {k: f["value"] for k, f in _extract(text, field_set)["extracted_fields"].items()}


def _fs(**fields) -> dict:
    """Bộ trường nhỏ dựng ngay trong test: `_fs(key={label, value_type, ...})`."""
    return {"fields_catalog": fields}


def test_normalize_text_collapses_whitespace():
    assert normalize_text("a  \t b\r\nc\n\n\n\nd") == "a b\nc\n\nd"


def test_clean_value_strips_ocr_junk():
    assert clean_value(", nội dung điều khoản") == "nội dung điều khoản"
    assert clean_value(",") is None
    assert clean_value("2025-06-01") == "2025-06-01"   # không phá ngày ISO
    assert clean_value({"amount": 1}) == {"amount": 1}  # không đụng dict tiền


def test_clean_value_rejects_template_fill_hints():
    # Chữ HƯỚNG DẪN ĐIỀN của biểu mẫu không phải giá trị khai báo -> None.
    for junk in ("Điền theo thực tế đăng ký", "Điền 0", "Ghi theo thực tế",
                 "Nêu rõ số lượng", "(nếu có)", "Kê khai đầy đủ"):
        assert clean_value(junk) is None, junk
    # Giá trị thật không bị vạ lây.
    for ok in ("Không áp dụng biện pháp bảo lãnh", "Ghi chú: trả theo đợt", "Kho số 3, Hà Nội"):
        assert clean_value(ok) == ok


# ---------------------------------------------------------------------------
# ĐUÔI DẪN CHIẾU tài liệu khác
# ---------------------------------------------------------------------------
_DIA_DIEM_DAY_DU = (
    "Kho số 3, Khu công nghiệp Quang Minh, Hà Nội theo phụ lục 02 "
    "của hợp đồng số 15/2025/HĐDV ký ngày 05/03/2025"
)
_DIA_DIEM = "Kho số 3, Khu công nghiệp Quang Minh, Hà Nội"


def test_labeled_text_cuts_reference_clause():
    """Đuôi 'theo phụ lục ... của hợp đồng số ... ký ngày ...' nói về VĂN BẢN NGUỒN,
    không phải nội dung của trường — để lọt thì giá trị kéo theo số và ngày của trường khác."""
    text = f"Địa điểm giao hàng: {_DIA_DIEM_DAY_DU}\nThời hạn giao hàng: 30 ngày"
    val, _ = _find_labeled_text(text, r"(địa\s*điểm\s*giao\s*hàng)", 300, ["Thời hạn"])
    assert val == _DIA_DIEM


def test_reference_clause_cut_survives_ocr_losing_diacritics():
    """OCR rụng dấu là ca THƯỜNG GẶP -> mốc cắt phải khớp cả 'theo phu luc'."""
    assert clean_value(fold_diacritics(_DIA_DIEM_DAY_DU)) == fold_diacritics(_DIA_DIEM)


def test_reference_clause_only_cuts_the_tail():
    """Giá trị MỞ ĐẦU bằng chính mệnh đề dẫn chiếu -> để nguyên cho bộ lọc rác quyết định."""
    assert clean_value("theo phụ lục 05 của hợp đồng") == "theo phụ lục 05 của hợp đồng"
    assert clean_value("Kho số 3, Hà Nội") == "Kho số 3, Hà Nội"


# ---------------------------------------------------------------------------
# GOLDEN — trích xuất bằng luật với bộ trường mặc định `hop_dong_mau`
# ---------------------------------------------------------------------------
def test_golden_hop_dong_mau(mau):
    c = _extract(_HD_DICH_VU, mau)
    ef = c["extracted_fields"]
    golden = {
        "so_hop_dong": "15/2025/HĐDV",                 # nhãn phụ 'Số'
        "ngay_ky": "2025-03-05",                        # nhãn phụ 'Hôm nay, ngày' + ngày CHỮ
        "ben_a": "Công ty TNHH Thương mại Minh Phát",
        "ben_b": "Công ty Cổ phần Dịch vụ Kỹ thuật An Bình",
        "so_luong": 12,
        "thoi_han": "12 tháng kể từ ngày ký",
        "phuong_thuc_thanh_toan": "Chuyển khoản, chia làm 2 đợt",
        "giai_quyet_tranh_chap": "Tòa án nhân dân có thẩm quyền tại Hà Nội",
    }
    for k, want in golden.items():
        assert ef[k]["value"] == want, f"{k}: {ef[k]['value']!r} != {want!r}"
    money = ef["gia_tri_hop_dong"]["value"]
    assert (money["amount"], money["currency"]) == (120_000_000, "VND")
    assert money["note"] == "Một trăm hai mươi triệu đồng"   # ghi chú trong ngoặc giữ lại
    assert c["missing_fields"] == [] and c["warnings"] == []
    # Mỗi giá trị có BẰNG CHỨNG và nhãn; thuộc tính trường lấy từ bộ trường.
    assert ef["ben_a"]["evidence"] == {"short_quote": golden["ben_a"], "source": "NORMALIZED_TEXT"}
    assert ef["ngay_ky"]["group"] == "declaration" and ef["thoi_han"]["group"] == "check"
    assert ef["so_luong"]["check_type"] == "positive_integer"
    assert ef["gia_tri_hop_dong"]["value_type"] == "money"
    assert c["document_type"] == "hợp đồng"
    assert c["contract_meta"]["field_set_id"] == "hop_dong_mau"
    assert c["contract_meta"]["signed_date_field"] == "ngay_ky"
    assert c["derived"]["signed_date"] == {"value": "2025-03-05", "confidence": 0.6,
                                           "from_field": "ngay_ky"}


def test_truong_khong_co_trong_van_ban_nam_trong_missing(mau):
    """Trường không có trong văn bản -> value None + missing_fields (không bịa), và khi
    gần như mọi trường đều trượt thì nhắc kiểm tra lại nhãn của bộ trường."""
    c = _extract("HỢP ĐỒNG DỊCH VỤ\n- Giá trị hợp đồng: 120.000.000 VND\n", mau)
    assert c["extracted_fields"]["gia_tri_hop_dong"]["value"]["amount"] == 120_000_000
    assert set(c["missing_fields"]) == set(mau["fields_catalog"]) - {"gia_tri_hop_dong"}
    assert c["extracted_fields"]["thoi_han"] == {
        "label": "Thời hạn", "value": None, "confidence": 0.0,
        "evidence": {"short_quote": None, "source": None}, "group": "check",
        "check_type": "regulated", "value_type": "text", "section": ""}
    assert not any("Hầu hết trường" in w for w in c["warnings"])   # 8/9 thiếu: chưa tới 90%
    trong = _extract("0000 1111 2222", mau)
    assert any("label_alts" in w for w in trong["warnings"])


def test_rules_tat_thi_moi_truong_deu_thieu(mau, monkeypatch):
    from app.core import settings

    monkeypatch.setattr(settings, "use_labeled_text_rules", False)
    assert set(_extract(_HD_DICH_VU, mau)["missing_fields"]) == set(mau["fields_catalog"])


# ---------------------------------------------------------------------------
# Từng KIỂU giá trị: date · number · money · text
# ---------------------------------------------------------------------------
def test_date_label_scans_all_occurrences(mau):
    """Nhãn trúng một câu KHÔNG có ngày trước (lời dẫn); phải duyệt tiếp tới lần có ngày."""
    text = ("Ngày ký: theo trang cuối của hợp đồng này, sau khi hai bên đã thống nhất toàn bộ\n"
            "- Bên A: Công ty Minh Phát\n- Ngày ký: 05/03/2025\n")
    assert _values(text, mau)["ngay_ky"] == "2025-03-05"


def test_date_khong_co_that_tren_lich_bi_loai(mau):
    assert _values("- Ngày ký: 31/02/2025\n", mau)["ngay_ky"] is None


def test_no_fabricated_date_from_free_text():
    """dateutil(fuzzy=True) từng BỊA ra ngày từ '8 giờ/ngày, 40 giờ/tuần'."""
    assert _try_parse_date_any("Thi gian thc hin: Không quá 8 gi/ngày, 40 gi/tun") is None
    assert _try_parse_date_any("bàn giao trong tháng 07/2025") == "2025-07-01"


def test_number_lay_so_sau_nhan_khong_lay_so_tien(mau):
    # Số thứ tự của mục ('6.') không phải giá trị; số tiền kèm đơn vị bị bỏ qua.
    assert _values("6. Số lượng: 8 bộ\n", mau)["so_luong"] == 8
    assert _values("Số lượng: 120.000.000 VND tương ứng 12 bộ\n", mau)["so_luong"] == 12


def test_money_label_neo_dau_dong_va_ky_tra(mau):
    """Nhãn NEO đầu dòng: cửa sổ tính từ SAU nhãn; kỳ trả và ghi chú là một phần giá trị."""
    v = _values("- Giá trị hợp đồng: 120.000.000 VND/tháng (đã gồm thuế GTGT)\n", mau)
    assert v["gia_tri_hop_dong"] == {"amount": 120_000_000, "currency": "VND", "period": "tháng",
                                     "raw": "120.000.000 VND", "note": "đã gồm thuế GTGT"}


def test_money_don_vi_bi_ngat_sang_dong_sau(mau):
    """OCR ngắt dòng giữa số và đơn vị ('50' / 'USD') — cửa sổ trải 2 dòng."""
    v = _values("Tổng giá trị hợp đồng: 50\nUSD\n- Thời hạn: 6 tháng\n", mau)["gia_tri_hop_dong"]
    assert (v["amount"], v["currency"]) == (50, "USD")


def test_heading_label_does_not_steal_next_line_amount():
    """'- Chi phí khác:' là TIÊU ĐỀ danh sách con (không có số). Không được nuốt '0 VNĐ'
    của dòng dưới; phải đi tiếp tới '+ Chi phí khác: 200.000'."""
    fs = _fs(chi_phi_khac={"label": "Chi phí khác", "value_type": "money"},
             van_chuyen={"label": "Chi phí vận chuyển", "value_type": "money"})
    v = _values("- Chi phí khác:\n+ Chi phí vận chuyển: 0 VNĐ\n"
                "+ Chi phí khác: 200.000 VNĐ (phí gửi hồ sơ)\n", fs)
    assert v["van_chuyen"]["amount"] == 0
    assert v["chi_phi_khac"]["amount"] == 200_000 and v["chi_phi_khac"]["note"] == "phí gửi hồ sơ"


def test_text_dung_o_nhan_cua_truong_ke_tiep(mau):
    """Hai mục chung một dòng: giá trị không được tràn sang nhãn của trường khác."""
    v = _values("- Thời hạn: 12 tháng, Phương thức thanh toán: chuyển khoản\n", mau)
    assert v["thoi_han"] == "12 tháng"
    assert v["phuong_thuc_thanh_toan"] == "chuyển khoản"


def test_text_label_chiu_loi_ocr(mau):
    """Bản scan rụng dấu: nhãn chịu lỗi OCR vẫn bắt được, độ tin cậy thấp hơn nhãn đúng."""
    f = _extract("- Gii quyt tranh chp: Tòa án nhân dân có thẩm quyền\n", mau)[
        "extracted_fields"]["giai_quyet_tranh_chap"]
    assert f["value"] == "Tòa án nhân dân có thẩm quyền" and f["confidence"] == 0.5


def test_label_tail_is_not_returned_as_value():
    """Nhãn dài bị OCR cắt cụt: phần ĐUÔI NHÃN còn lại không được trả về làm giá trị."""
    fs = _fs(nghiem_thu={"label": "Điều kiện nghiệm thu bàn giao sản phẩm", "value_type": "text"})
    v = _values("- Điu kin nghim thu bàn giao sn phm theo quy đnh ca hp đng: "
                "Biên bản ký trong 5 ngày\n", fs)
    assert v["nghiem_thu"] == "Biên bản ký trong 5 ngày"


def test_nhan_dieu_khoan_sau_so_hieu_dieu(mau):
    """'Điều 8. Giải quyết tranh chấp: ...' — nhãn đứng sau số hiệu điều vẫn bắt được."""
    v = _values("Điều 8. Giải quyết tranh chấp: Tòa án nhân dân thành phố Hà Nội\n", mau)
    assert v["giai_quyet_tranh_chap"] == "Tòa án nhân dân thành phố Hà Nội"


# ---------------------------------------------------------------------------
# value_regex · max_len · nhãn trùng · nhãn phụ
# ---------------------------------------------------------------------------
_FS_REGEX = _fs(
    ma_so_thue={"label": "Mã số thuế", "value_type": "number",
                "value_regex": r"MST\s*:\s*(\d{10})"},
    so_hd={"label": "Số hợp đồng", "value_type": "text",
           "value_regex": r"\d{1,4}/\d{4}/[A-ZĐ\-]+"},
)


def test_value_regex_truong_khong_phai_van_ban_do_khuon_tren_toan_van():
    """Mã số không cần nhãn: khuôn giá trị rõ hơn nhãn. Lấy đúng nhóm bắt được."""
    f = _extract("HỢP ĐỒNG DỊCH VỤ\nMST: 0316075160\n", _FS_REGEX)["extracted_fields"]["ma_so_thue"]
    assert f["value"] == "0316075160" and f["confidence"] == 0.7
    assert f["evidence"]["short_quote"] == "MST: 0316075160"


def test_value_regex_truong_van_ban_cat_dung_khuon_va_loai_sai_dang():
    """Trường văn bản có `value_regex`: chỉ giữ phần đúng khuôn; câu dẫn chiếu bị loại."""
    assert _values("Số hợp đồng: 15/2025/HĐDV ngày 05/03/2025\n", _FS_REGEX)["so_hd"] == "15/2025/HĐDV"
    assert _values("Số hợp đồng: xem phụ lục kèm theo\n", _FS_REGEX)["so_hd"] is None


def test_max_len_giu_cau_co_tu_khoa():
    fs = _fs(bao_hanh={"label": "Bảo hành", "value_type": "text", "max_len": 80,
                       "keywords": ["bao hanh"]})
    v = _values("Bảo hành: Bên B bảo hành thiết bị trong 12 tháng kể từ ngày nghiệm thu. "
                "Hai bên có thể thỏa thuận thêm bằng văn bản riêng nếu cần. "
                "Chi phí bảo hành do bên B chịu toàn bộ.\n", fs)["bao_hanh"]
    assert v == "Bên B bảo hành thiết bị trong 12 tháng kể từ ngày nghiệm thu"


def test_nhan_trung_giua_hai_truong_khong_dinh_danh_truong_nao():
    """Hai trường cùng nhãn chính -> nhãn đó bị bỏ; trường có nhãn phụ riêng vẫn bắt được."""
    fs = _fs(a={"label": "Ghi chú", "value_type": "text"},
             b={"label": "Ghi chú", "label_alts": ["Ghi chú khác"], "value_type": "text"})
    c = _extract("- Ghi chú: hàng giao tại kho\n- Ghi chú khác: bảo hành 12 tháng\n", fs)
    assert c["extracted_fields"]["a"]["value"] is None
    assert c["extracted_fields"]["b"]["value"] == "bảo hành 12 tháng"
    assert c["missing_fields"] == ["a"]


# ---------------------------------------------------------------------------
# NGÀY KÝ suy ra (derived.signed_date)
# ---------------------------------------------------------------------------
_FS_NGAY_KY = {"signed_date_field": "ngay_ky", "fields_catalog": {
    "ngay_ky": {"label": "Ngày ký", "value_type": "date"},
    "ben_a": {"label": "Bên A", "value_type": "text"}}}


def test_ngay_ky_suy_ra_tu_ngay_dau_tien_khi_truong_trong():
    c = _extract("Hà Nội, 05/03/2025\nBên A: Công ty Minh Phát\n", _FS_NGAY_KY)
    assert c["extracted_fields"]["ngay_ky"]["value"] is None
    assert c["derived"]["signed_date"] == {"value": "2025-03-05", "confidence": 0.25,
                                           "from_field": None}
    assert any("độ tin cậy thấp" in w for w in c["warnings"])


def test_bo_truong_khong_khai_ngay_ky_thi_khong_suy_ra():
    """Không khai `signed_date_field` -> không lọc quy định theo ngày, không đoán ngày."""
    c = _extract("Hà Nội, 05/03/2025\nBên A: Công ty Minh Phát\n", _fs(**_FS_NGAY_KY["fields_catalog"]))
    assert c["derived"]["signed_date"] == {"value": None, "confidence": 0.0, "from_field": None}
    assert c["contract_meta"]["signed_date_field"] == "" and c["document_type"] == "document"


@pytest.mark.parametrize("nhap,mong", [
    ("2025-03-01", "2025-03-01"),
    ("2025-3-1", "2025-03-01"),
    ("01/03/2025", "2025-03-01"),
    ("1-3-2025", "2025-03-01"),
    ("ngày 01 tháng 3 năm 2025", "2025-03-01"),
    ("", ""), (None, ""), ("hôm nọ", ""), ("2025-13-01", ""),
])
def test_nan_ngay_ky_nguoi_dung_nhap(nhap, mong):
    """Ngày ký đi thẳng vào bộ lọc hiệu lực văn bản, nơi `date_to_int` chỉ gom chữ số
    theo thứ tự xuất hiện: '01/03/2025' để nguyên thành mốc 1032025."""
    assert normalize_signed_date(nhap) == mong


# ---------------------------------------------------------------------------
# Nhãn CHỊU LỖI OCR + lọc giá trị
# ---------------------------------------------------------------------------
def test_ocr_tolerant_label_matches_dropped_vowels():
    rx = _ocr_tolerant_label_regex("Phương thức thanh toán")
    assert re.search(rx, "phng thc thanh toan: chuyen khoan", re.IGNORECASE)
    assert re.search(rx, "phuong thuc thanh toan: chuyen khoan", re.IGNORECASE)
    assert not re.search(rx, "thoi han thanh toan", re.IGNORECASE)


def test_ocr_tolerant_label_covers_substitution_errors():
    """Nhãn chịu lỗi OCR phải phủ CẢ HAI kiểu hỏng: RỤNG nguyên âm có dấu và THAY ký tự
    bằng chữ gần giống ('ư' -> 'v', 'ơ' -> 'o', 'đ' -> 'd'). Trượt nhãn thì rule im lặng
    trả rỗng, không lỗi nào được ghi ra."""
    def hit(label: str, line: str) -> bool:
        rx = _ocr_tolerant_label_regex(label, anchor=True)
        return bool(rx and re.search(rx, "\n" + fold_diacritics(line), re.IGNORECASE))

    # PHẢI bắt được — cả bản rụng ký tự lẫn bản thay ký tự của cùng một nhãn.
    assert hit("Giá trị hợp đồng", "- Gia tr hp dng: 120.000.000 VND")
    assert hit("Giá trị hợp đồng", "- Gía tri hop đông: 120.000.000 VND")
    assert hit("Phương thức thanh toán", "4. Phvong thirc thanh toan: chuyen khoan")
    assert hit("Giải quyết tranh chấp", "- Gii quyt tranh chp: Toa an")
    assert hit("Giải quyết tranh chấp", "- Giai quyêt tranh chap: Toa an")
    assert hit("Số lượng", "- S6 lvong: 12")

    # KHÔNG được khớp bừa — nới nhãn mà mất tính phân biệt thì còn tệ hơn trượt.
    assert not hit("Giá trị hợp đồng", "- Thoi han hop dong: 12 thang")
    assert not hit("Phương thức thanh toán", "- Thoi han thanh toan: 30 ngay")
    assert not hit("Giải quyết tranh chấp", "- Giai doan thuc hien: 2 dot")


def test_text_value_filter_rejects_money_and_junk():
    assert _is_junk_text_value("0 VND")
    assert _is_junk_text_value("1.200 USD/thang")
    assert _is_junk_text_value("Tổng cộng: 0 bộ")
    assert _is_junk_text_value("   ")
    assert not _is_junk_text_value("Kho số 3, Hà Nội")
    assert not _is_junk_text_value("12 tháng")


# ---------------------------------------------------------------------------
# ĐIỀU KHOẢN DÀI — giữ câu có nội dung thay vì cắt cụt, và không dừng ở câu dẫn chiếu
# ---------------------------------------------------------------------------
_BAO_HANH = (
    "Bên B bảo hành thiết bị miễn phí trong 12 tháng. "
    "Hai bên có thể thỏa thuận thêm bằng văn bản riêng nếu thấy cần thiết. "
    "Chi phí vận chuyển khi bảo hành do bên A tự chi trả theo thực tế phát sinh. "
    "Linh kiện thay thế được bên B hỗ trợ 50% chi phí."
)


def test_doan_qua_dai_giu_tron_cau_co_tu_khoa():
    """Cắt cứng ở ký tự thứ N làm giá trị đứt giữa từ và thường mất đúng phần nội
    dung. Giữ NGUYÊN VĂN từng câu, ưu tiên câu chứa từ khóa của trường."""
    out = _condense_clause(_BAO_HANH, ["bao hanh", "van chuyen"], 150)
    assert len(out) <= 150
    assert out.endswith(("sinh", "tháng")), f"không được đứt giữa từ: {out!r}"
    assert "bảo hành thiết bị miễn phí" in out and "Chi phí vận chuyển" in out
    assert "thỏa thuận thêm bằng văn bản riêng" not in out, "câu không liên quan phải rụng"


def test_doan_ngan_giu_nguyen_van():
    ngan = "Bên B bảo hành thiết bị trong 12 tháng."
    assert _condense_clause(ngan, ["bao hanh"], 300) == ngan


def test_khong_tu_khoa_nao_khop_thi_giu_cac_cau_dau():
    out = _condense_clause(_BAO_HANH, ["khong-co-tu-nay"], 120)
    assert out.startswith("Bên B bảo hành thiết bị miễn phí")
    assert len(out) <= 120


def test_mot_cau_dai_hon_gioi_han_van_cat_o_ranh_gioi_tu():
    out = _condense_clause("Bên B " + "rất " * 60 + "cẩn thận", [], 50)
    assert len(out) <= 50 and not out.endswith("r")


def test_nhan_dien_cau_dan_chieu_chung():
    """'Theo quy định của pháp luật' đúng hình thức nhưng rỗng nội dung — phải xếp SAU
    mọi đoạn cụ thể hơn, chứ không phải bị loại bỏ."""
    for chung in ("Theo quy định của pháp luật Việt Nam",
                  "Thực hiện theo quy định của pháp luật hiện hành",
                  "Theo luật hiện hành",
                  "Áp dụng theo chính sách của công ty"):
        assert is_boilerplate_clause(chung), chung
    for cu_the in ("Tòa án nhân dân có thẩm quyền tại Hà Nội",
                   "Chuyển khoản, chia làm 2 đợt",
                   "Theo quy định của pháp luật Việt Nam, tranh chấp được giải quyết tại "
                   "Trung tâm Trọng tài Quốc tế Việt Nam theo quy tắc tố tụng của trung tâm"):
        assert not is_boilerplate_clause(cu_the), cu_the


def test_uu_tien_doan_cu_the_hon_cau_dan_chieu(mau):
    """Nhãn xuất hiện HAI chỗ: chỗ đầu chỉ ghi câu dẫn chiếu, chỗ sau ghi nội dung thật."""
    text = ("- Giải quyết tranh chấp: Theo quy định của pháp luật Việt Nam\n"
            "\n"
            "Giải quyết tranh chấp: Tòa án nhân dân có thẩm quyền tại Hà Nội.\n")
    assert "Tòa án nhân dân" in _values(text, mau)["giai_quyet_tranh_chap"]


def test_chi_co_cau_dan_chieu_thi_van_lay_lam_gia_tri(mau):
    """Xếp sau KHÁC với loại bỏ: hồ sơ chỉ ghi đúng câu đó thì nó vẫn là giá trị thật."""
    text = "- Giải quyết tranh chấp: Theo quy định của pháp luật Việt Nam\n"
    assert "pháp luật Việt Nam" in _values(text, mau)["giai_quyet_tranh_chap"]


# ---------------------------------------------------------------------------
# MERGE LLM — chống bịa
# ---------------------------------------------------------------------------
_CATALOG = {
    "dia_diem": {"label": "Địa điểm giao hàng", "value_type": "text"},
    "so_luong": {"label": "Số lượng", "value_type": "number"},
    "ngay_ky": {"label": "Ngày ký", "value_type": "date"},
    "gia_tri": {"label": "Giá trị hợp đồng", "value_type": "money"},
}


def _contract(text: str, **values) -> dict:
    ef = {k: {"value": values.get(k), "label": e["label"], "confidence": 0.6 if k in values else 0.0}
          for k, e in _CATALOG.items()}
    return {"extracted_fields": ef, "missing_fields": [k for k in _CATALOG if k not in values],
            "raw": {"normalized_text": text}}


def _merge(text: str, llm: dict, **kw) -> dict:
    values = kw.pop("values", {})
    return merge_llm_extraction(_contract(text, **values), llm, _CATALOG, **kw)


def test_typed_keys_va_money_keys():
    assert typed_keys(_CATALOG) == ({"so_luong"}, {"ngay_ky"})
    assert money_keys(_CATALOG) == {"gia_tri"}


def test_merge_llm_accepts_value_with_evidence():
    text = "Bên B giao hàng tại kho số 3, Khu công nghiệp Quang Minh, Hà Nội theo lịch."
    out = _merge(text, {"dia_diem": {"value": "kho số 3, Khu công nghiệp Quang Minh, Hà Nội",
                                     "evidence_quote": "giao hàng tại kho số 3",
                                     "confidence": 1.0}})
    f = out["extracted_fields"]["dia_diem"]
    assert f["value"] == "kho số 3, Khu công nghiệp Quang Minh, Hà Nội"
    assert f["confidence"] <= 0.7          # trần conf cho giá trị LLM (chặn 1.0 giả)
    assert f["evidence"]["source"] == "LLM_EXTRACTION"
    assert "dia_diem" not in out["missing_fields"]


def test_merge_llm_rejects_hallucinated_value():
    # Bằng chứng KHÔNG có trong văn bản -> coi là bịa -> loại, trường vẫn thiếu.
    text = "Văn bản này là biên bản nghiệm thu, không nói gì về nơi giao hàng."
    out = _merge(text, {"dia_diem": {"value": "kho Bình Dương",
                                     "evidence_quote": "giao hàng tại kho Bình Dương",
                                     "confidence": 1.0}})
    assert out["extracted_fields"]["dia_diem"]["value"] is None
    assert "dia_diem" in out["missing_fields"]


def test_merge_llm_min_confidence():
    text = "Bên B giao hàng tại kho số 3, Hà Nội."
    llm = {"dia_diem": {"value": "kho số 3, Hà Nội", "evidence_quote": "giao hàng tại kho số 3",
                        "confidence": 0.3}}
    assert _merge(text, llm, min_confidence=0.5)["extracted_fields"]["dia_diem"]["value"] is None
    assert _merge(text, llm)["extracted_fields"]["dia_diem"]["value"] == "kho số 3, Hà Nội"


def test_merge_llm_never_overwrites_regex_value():
    text = "Ngày ký 01/06/2025 tại Hà Nội."
    out = _merge(text, {"dia_diem": {"value": "Đà Nẵng", "evidence_quote": "Ngày ký 01/06/2025",
                                     "confidence": 0.9}}, values={"dia_diem": "Hà Nội"})
    assert out["extracted_fields"]["dia_diem"]["value"] == "Hà Nội"


def test_merge_llm_bo_placeholder_va_truong_la():
    text = "Giao hàng tại kho số 3, Hà Nội."
    out = _merge(text, {"dia_diem": {"value": "Không ghi", "evidence_quote": "Giao hàng tại kho",
                                     "confidence": 0.9},
                        "khong_co_trong_bo_truong": {"value": "x", "confidence": 0.9}})
    assert out["extracted_fields"]["dia_diem"]["value"] is None
    assert "khong_co_trong_bo_truong" not in out["extracted_fields"]


def test_merge_llm_ep_kieu_so_va_ngay():
    """Trường SỐ không nhận số tiền; trường NGÀY phải parse được về ISO."""
    text = "Số lượng: 12 bộ. Giá trị 120.000.000 VND. Ký ngày 05/03/2025."
    ok = _merge(text, {"so_luong": {"value": "12 bộ", "evidence_quote": "Số lượng: 12 bộ"}})
    assert ok["extracted_fields"]["so_luong"]["value"] == 12
    bad = _merge(text, {"so_luong": {"value": "120.000.000 VND",
                                     "evidence_quote": "Giá trị 120.000.000 VND"},
                        "ngay_ky": {"value": "đầu năm", "evidence_quote": "Ký ngày 05/03/2025"}})
    assert bad["extracted_fields"]["so_luong"]["value"] is None
    assert bad["extracted_fields"]["ngay_ky"]["value"] is None


def test_merge_llm_money_phai_la_so_va_co_trong_van_ban():
    text = "Giá trị hợp đồng: 120.000.000 VND. Phí bảo hiểm: không thu."
    ok = _merge(text, {"gia_tri": {"value": {"amount": 120000000, "currency": "VND",
                                             "period": "performed"},
                                   "evidence_quote": "Giá trị hợp đồng: 120.000.000 VND"}})
    assert ok["extracted_fields"]["gia_tri"]["value"] == {
        "amount": 120000000, "currency": "VND", "period": None}     # kỳ trả rác bị bỏ
    bia = _merge(text, {"gia_tri": {"value": {"amount": 150000000, "currency": "VND"},
                                    "evidence_quote": "Giá trị hợp đồng: 120.000.000 VND"}})
    assert bia["extracted_fields"]["gia_tri"]["value"] is None
    khong = _merge(text, {"gia_tri": {"value": {"amount": "Không có", "currency": "VND"},
                                      "evidence_quote": "Phí bảo hiểm: không thu"}})
    assert khong["extracted_fields"]["gia_tri"]["value"]["amount"] == 0


def test_merge_llm_rejects_money_value_for_text_field():
    """Trường VĂN BẢN nhận '0 VND' (model đọc nhầm sang khối chi phí) -> loại."""
    text = "Dia diem giao hang: kho cua ben A. Phi van chuyen: 0 VND"
    for val in ("0 VND", {"amount": 0, "currency": "VND", "raw": "0 VND"}):
        out = _merge(text, {"dia_diem": {"value": val, "evidence_quote": "Phi van chuyen: 0 VND",
                                         "confidence": 0.9}})
        assert out["extracted_fields"]["dia_diem"]["value"] is None


def test_merge_llm_rejects_value_taken_from_another_field():
    """Bằng chứng CÓ THẬT nhưng giá trị lấy từ dòng khác -> loại (không nhận bừa)."""
    text = ("Dia diem giao hang: Kho so 3 Quang Minh (theo lich). "
            "So luong: 8 bo may phat dien du phong")
    out = _merge(text, {"dia_diem": {"value": "8 bộ máy phát điện tại kho trung tâm Hải Phòng",
                                     "evidence_quote": "So luong: 8 bo may phat dien",
                                     "confidence": 0.9}})
    assert out["extracted_fields"]["dia_diem"]["value"] is None


def test_merge_llm_rejects_english_and_unwraps_object():
    """Model trả object + đơn vị tiếng Anh cho trường văn bản -> loại (giữ tiếng Việt)."""
    text = "Dia diem giao hang: giao trong 90 phut/ngay tai kho so 3"
    out = _merge(text, {"dia_diem": {"value": {"amount": 90, "currency": "Minutes",
                                               "raw": "90 minutes/day"},
                                     "evidence_quote": "giao trong 90 phut/ngay"}})
    assert out["extracted_fields"]["dia_diem"]["value"] is None
    ok = _merge(text, {"dia_diem": {"value": {"raw": "tại kho số 3"},
                                    "evidence_quote": "giao trong 90 phut/ngay tai kho so 3"}})
    assert ok["extracted_fields"]["dia_diem"]["value"] == "tại kho số 3"


def test_respell_fixes_spelling_but_not_content():
    """LLM chỉ được sửa CHÍNH TẢ trường đã có giá trị regex; đổi nội dung -> giữ bản gốc."""
    text = "Dia dim giao hang: Kho so 3 Quang Minh (theo lich)."
    ok = _merge(text, {"dia_diem": {"value": "Kho số 3 Quang Minh",
                                    "evidence_quote": "Kho so 3 Quang Minh", "confidence": 0.5}},
                values={"dia_diem": "Kho so 3 Quang Minh"}, respell_keys={"dia_diem"})
    f = ok["extracted_fields"]["dia_diem"]
    assert f["value"] == "Kho số 3 Quang Minh" and f["evidence"]["source"] == "LLM_RESPELL"
    assert f["confidence"] == 0.6           # giữ độ tin cậy cũ (cao hơn) của regex

    bad = _merge(text, {"dia_diem": {"value": "Kho số 8 Hải Phòng theo lịch giao",
                                     "evidence_quote": "Kho so 3 Quang Minh", "confidence": 0.8}},
                 values={"dia_diem": "Kho so 3 Quang Minh"}, respell_keys={"dia_diem"})
    assert bad["extracted_fields"]["dia_diem"]["value"] == "Kho so 3 Quang Minh"


# ---------------------------------------------------------------------------
# pipeline.process_file — OCR giả, LLM giả
# ---------------------------------------------------------------------------
def _fake_read(text: str):
    def _read(data, job_prompt, source_file, *a, **k):
        return {"pages": [], "page_meta": [], "full_text": text,
                "stats": {"num_pages": 1, "num_lines": 40, "avg_confidence": 0.95,
                          "low_conf_lines": 0}}
    return _read


@pytest.fixture()
def pipeline_gia(monkeypatch):
    from app.core import settings
    from app.domain.documents import pipeline, spelling

    # Từ điển khôi phục dấu dựng từ CHÍNH văn bản mẫu: mọi từ đều "đã biết" nên bước
    # sửa dấu giữ nguyên giá trị — test này kiểm điều phối, không kiểm từ điển.
    monkeypatch.setattr(spelling, "_corpus_texts", lambda: [_HD_DICH_VU] * 2)
    spelling.reset_lexicon()
    monkeypatch.setattr(settings, "llm_warmup", False)
    monkeypatch.setattr(pipeline, "progress_update", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_read_document", _fake_read(_HD_DICH_VU * 2))
    yield pipeline
    spelling.reset_lexicon()


def test_process_file_khong_mo_hinh(pipeline_gia, mau, monkeypatch):
    from app.core import settings

    monkeypatch.setattr(settings, "use_llm_extraction", False)
    out = asyncio.run(pipeline_gia.process_file("s1", b"%PDF", "hop dong.pdf", mau))
    c = out["contract"]
    assert out["source_file"] == "hop dong.pdf" and out["missing_fields"] == []
    assert c["extracted_fields"]["ngay_ky"]["value"] == "2025-03-05"
    assert c["contract_meta"]["created_at"] and "completeness" in c
    assert c["input_flags"] == []           # OCR tốt, ngày ký hợp lệ -> không cờ nào


def test_process_file_mo_hinh_chi_bu_truong_thieu(pipeline_gia, monkeypatch):
    """Mô hình nhận ĐÚNG phần luật chưa làm được (trường thiếu + trường văn bản cần sửa
    chính tả); giá trị bù phải qua lớp chống bịa."""
    from app.core import settings

    monkeypatch.setattr(settings, "use_llm_extraction", True)
    fs = _fs(ben_a={"label": "Bên A", "value_type": "text"},
             dia_diem={"label": "Địa điểm giao hàng", "value_type": "text"})
    seen = {}

    async def fake_llm(job_prompt, normalized, only_keys):
        seen["keys"] = set(only_keys)
        return {"dia_diem": {"value": "Tòa án nhân dân có thẩm quyền tại Hà Nội",
                             "evidence_quote": "Tòa án nhân dân có thẩm quyền tại Hà Nội",
                             "confidence": 0.9}}, {}

    monkeypatch.setattr(pipeline_gia, "run_llm_extraction", fake_llm)
    c = asyncio.run(pipeline_gia.process_file("s1", b"%PDF", "hop dong.pdf", fs))["contract"]
    assert seen["keys"] == {"dia_diem", "ben_a"}
    assert c["extracted_fields"]["dia_diem"]["evidence"]["source"] == "LLM_EXTRACTION"
    assert c["extracted_fields"]["ben_a"]["value"] == "Công ty TNHH Thương mại Minh Phát"
    assert c["missing_fields"] == []


def test_process_file_mo_hinh_loi_chi_canh_bao(pipeline_gia, mau, monkeypatch):
    from app.core import settings

    monkeypatch.setattr(settings, "use_llm_extraction", True)

    async def boom(*a, **k):
        raise RuntimeError("mô hình hỏng")

    monkeypatch.setattr(pipeline_gia, "run_llm_extraction", boom)
    c = asyncio.run(pipeline_gia.process_file("s1", b"%PDF", "hop dong.pdf", mau))["contract"]
    assert any("mô hình hỏng" in w for w in c["warnings"])
    assert c["extracted_fields"]["so_hop_dong"]["value"] == "15/2025/HĐDV"


# ---------------------------------------------------------------------------
# HỒI QUY: các lỗi nhận nhãn / ngắt giá trị / kiểm bằng chứng ngày đã sửa.
# ---------------------------------------------------------------------------
def test_nhan_ngan_khong_khop_nhan_khac(mau):
    v = _values("HỢP ĐỒNG DỊCH VỤ\n- Phương thức thanh toán: Chuyển khoản\n"
                "- Bên A: Công ty TNHH Minh Phát\n", mau)
    assert v["ben_b"] is None
    assert v["thoi_han"] is None
    v = _values("Văn bản không có nhãn nào.", mau)          # 'bản' ≡ 'Bên A' ≡ 'Bên B'
    assert v["ben_a"] is None and v["ben_b"] is None


def test_moc_dung_khong_cat_o_chu_khong(mau):
    v = _values("- Giải quyết tranh chấp: Hai bên thương lượng; không thành thì đưa ra "
                "Tòa án nhân dân có thẩm quyền\n", mau)
    assert "Tòa án" in v["giai_quyet_tranh_chap"]


def test_gia_tri_dung_truoc_tieu_de_dieu(mau):
    v = _values("- Bên B: Công ty Cổ phần An Bình\nĐiều 1. Nội dung dịch vụ\n", mau)
    assert v["ben_b"] == "Công ty Cổ phần An Bình"


def test_merge_llm_nhan_ngay_co_bang_chung():
    text = "Hai bên ký ngày 05/03/2025 tại Hà Nội."
    out = _merge(text, {"ngay_ky": {"value": "05/03/2025", "evidence_quote": "ký ngày 05/03/2025"}})
    assert out["extracted_fields"]["ngay_ky"]["value"] == "2025-03-05"
