"""Test trích xuất thuần: chuẩn hóa text, làm sạch giá trị, chống-bịa khi merge LLM."""
import pytest

from app.domain.documents.enrich import merge_llm_extraction
from app.domain.documents.rules import (
    _find_labeled_text,
    clean_value,
    normalize_signed_date,
    normalize_text,
)


def test_normalize_text_collapses_whitespace():
    assert normalize_text("a  \t b\r\nc\n\n\n\nd") == "a b\nc\n\nd"


def test_clean_value_strips_ocr_junk():
    assert clean_value(", tiền công của người lao động") == "tiền công của người lao động"
    assert clean_value(",") is None
    assert clean_value("2025-06-01") == "2025-06-01"   # không phá ngày ISO
    assert clean_value({"amount": 1}) == {"amount": 1}  # không đụng dict tiền


# ---------------------------------------------------------------------------
# ĐUÔI DẪN CHIẾU — hồ sơ thật MISUGA KAIUN × VCSCO (20/06/2024)
# ---------------------------------------------------------------------------
_MISUGA = (
    "Trên các tàu: Princess Lily, Princess Haru, Princess Suiha theo phụ lục 05 "
    'của hợp đồng cung ứng số "No.04/Contract 24.06." giữa công ty '
    "MISUGA KAIUN CO., LTD và Công ty VCSCO ký ngày 20/06/2024"
)
_NOI_TAU = "Trên các tàu: Princess Lily, Princess Haru, Princess Suiha"


def test_labeled_text_cuts_reference_clause():
    """Địa điểm làm việc chỉ là TÊN NƠI LÀM VIỆC. Đuôi 'theo phụ lục ... của hợp đồng
    cung ứng số ... ký ngày ...' nói về VĂN BẢN NGUỒN, không phải nội dung của trường —
    để lọt thì giá trị vừa sai vừa kéo theo số hợp đồng và ngày ký của trường khác."""
    text = f"Địa điểm làm việc: {_MISUGA}\nThời giờ làm việc: 8 giờ/ngày"
    val, _ = _find_labeled_text(text, r"(địa\s*điểm\s*làm\s*việc)", 300, ["Thời giờ"])
    assert val == _NOI_TAU


def test_reference_clause_cut_survives_ocr_losing_diacritics():
    """OCR rụng dấu là ca THƯỜNG GẶP, không phải ngoại lệ -> mốc cắt phải khớp cả
    'theo phu luc'. Dò trên bản bỏ dấu nhưng cắt chuỗi GỐC nên dấu được giữ nguyên."""
    from app.domain.documents.ocr import fold_diacritics

    assert clean_value(fold_diacritics(_MISUGA)) == fold_diacritics(_NOI_TAU)


def test_selection_rejects_country_outside_market():
    """Quốc gia không thuộc thị trường đã chọn là lỗi NGƯỜI DÙNG -> `SelectionError`,
    không phải HTTPException: tầng domain phải chạy được ngoài web (CLI, test)."""
    import pytest

    from app.domain.documents.intake import SelectionError, resolve_selection

    with pytest.raises(SelectionError):
        resolve_selection(market="nhat_ban", country="khong_co_quoc_gia_nay")
    with pytest.raises(SelectionError):     # chọn "khác" mà bỏ trống ô nhập
        resolve_selection(market="khac", market_other="aaaa")


def test_selection_defaults_when_only_job_id_given():
    """Client cũ chỉ truyền `job_id` vẫn phải chạy y như trước khi có 3 nút chọn."""
    from app.domain.documents.intake import resolve_selection

    sel = resolve_selection(job_id="nhat_ban")
    assert sel.job_id == "nhat_ban" and sel.job_prompt.get("fields_catalog")
    assert sel.market_name == "" and sel.country_keywords == []


def test_reference_clause_only_cuts_the_tail():
    """Giá trị MỞ ĐẦU bằng chính mệnh đề dẫn chiếu thì không còn gì để giữ -> để
    nguyên cho bộ lọc rác quyết định, không tự cắt thành chuỗi rỗng."""
    assert clean_value("theo phụ lục 05 của hợp đồng") == "theo phụ lục 05 của hợp đồng"
    assert clean_value("Nhà máy Osaka") == "Nhà máy Osaka"        # không dính dẫn chiếu


def _contract(text: str) -> dict:
    return {
        "extracted_fields": {"noi_lam_viec": {"value": None, "label": "Nơi làm việc"}},
        "missing_fields": ["noi_lam_viec"],
        "raw": {"normalized_text": text},
    }


def test_merge_llm_accepts_value_with_evidence():
    text = "Người lao động làm việc tại nhà máy Osaka, Nhật Bản theo hợp đồng."
    llm = {"noi_lam_viec": {"value": "nhà máy Osaka, Nhật Bản",
                            "evidence_quote": "làm việc tại nhà máy Osaka, Nhật Bản",
                            "confidence": 1.0}}
    out = merge_llm_extraction(_contract(text), llm)
    f = out["extracted_fields"]["noi_lam_viec"]
    assert f["value"] == "nhà máy Osaka, Nhật Bản"
    assert f["confidence"] <= 0.7          # trần conf cho giá trị LLM (chặn 1.0 giả)
    assert "noi_lam_viec" not in out["missing_fields"]


def test_merge_llm_rejects_hallucinated_value():
    # Bằng chứng KHÔNG có trong văn bản -> coi là bịa -> loại, trường vẫn thiếu.
    text = "Văn bản này là giấy phép hoạt động dịch vụ, không nói gì về nơi làm việc."
    llm = {"noi_lam_viec": {"value": "nhà máy Quảng Châu, Trung Quốc",
                            "evidence_quote": "làm việc tại nhà máy Quảng Châu",
                            "confidence": 1.0}}
    out = merge_llm_extraction(_contract(text), llm)
    assert out["extracted_fields"]["noi_lam_viec"]["value"] is None
    assert "noi_lam_viec" in out["missing_fields"]


def test_merge_llm_never_overwrites_regex_value():
    contract = _contract("Ngày ký 01/06/2025 tại Hà Nội.")
    contract["extracted_fields"]["noi_lam_viec"] = {"value": "Hà Nội", "label": "Nơi làm việc"}
    contract["missing_fields"] = []
    llm = {"noi_lam_viec": {"value": "Tokyo", "evidence_quote": "Ngày ký 01/06/2025", "confidence": 0.9}}
    out = merge_llm_extraction(contract, llm)
    assert out["extracted_fields"]["noi_lam_viec"]["value"] == "Hà Nội"


def _text_field_contract(key: str, text: str, value=None) -> dict:
    return {
        "extracted_fields": {key: {"value": value, "label": key}},
        "missing_fields": [key] if value is None else [],
        "raw": {"normalized_text": text},
    }


def test_merge_llm_rejects_money_value_for_text_field():
    """Trường VĂN BẢN nhận '0 VND' (model đọc nhầm sang khối chi phí) -> loại."""
    text = ("An toan, v sinh lao dong: Nguoi su dung lao dong phai to chuc huan luyen. "
            "Tien dich vu: 0 VND")
    for val in ("0 VND", {"amount": 0, "currency": "VND", "raw": "0 VND"}):
        out = merge_llm_extraction(
            _text_field_contract("an_toan_ve_sinh_lao_dong", text),
            {"an_toan_ve_sinh_lao_dong": {
                "value": val, "evidence_quote": "Tien dich vu: 0 VND", "confidence": 0.9}},
        )
        assert out["extracted_fields"]["an_toan_ve_sinh_lao_dong"]["value"] is None


def test_merge_llm_rejects_value_taken_from_another_field():
    """Bằng chứng CÓ THẬT nhưng giá trị lấy từ dòng khác -> loại (không nhận bừa)."""
    text = ("Dia diem lam viec: Tren tau ca Captain Alex (IMO: Khong). "
            "Nganh, nghe: Thuyen vien tau ca xa bo, trong do so co nghe: 8")
    out = merge_llm_extraction(
        _text_field_contract("dia_diem_lam_viec", text),
        {"dia_diem_lam_viec": {
            "value": "8 tàu cá xa bờ (tuỳ theo sự phân công của chủ sử dụng)",
            "evidence_quote": "Nganh, nghe: Thuyen vien tau ca xa bo",
            "confidence": 0.9}},
    )
    assert out["extracted_fields"]["dia_diem_lam_viec"]["value"] is None


def test_respell_fixes_spelling_but_not_content():
    """LLM chỉ được sửa CHÍNH TẢ trường đã có giá trị regex; đổi nội dung -> giữ bản gốc."""
    text = "Dia dim lam vic: Tren tau ca Captain Alex (IMO: Khong)."
    key = "dia_diem_lam_viec"
    ok = merge_llm_extraction(
        _text_field_contract(key, text, value="Tren tau ca Captain Alex"),
        {key: {"value": "Trên tàu cá Captain Alex",
               "evidence_quote": "Tren tau ca Captain Alex", "confidence": 0.8}},
        respell_keys={key},
    )
    assert ok["extracted_fields"][key]["value"] == "Trên tàu cá Captain Alex"

    bad = merge_llm_extraction(
        _text_field_contract(key, text, value="Tren tau ca Captain Alex"),
        {key: {"value": "8 tàu cá xa bờ theo phân công của chủ sử dụng",
               "evidence_quote": "Tren tau ca Captain Alex", "confidence": 0.8}},
        respell_keys={key},
    )
    assert bad["extracted_fields"][key]["value"] == "Tren tau ca Captain Alex"


# ---------------------------------------------------------------------------
# Nhãn CHỊU LỖI OCR (rụng nguyên âm có dấu) + lọc giá trị
# ---------------------------------------------------------------------------
def test_ocr_tolerant_label_matches_dropped_vowels():
    import re

    from app.domain.documents.rules import _ocr_tolerant_label_regex
    rx = _ocr_tolerant_label_regex("Địa điểm làm việc")
    assert re.search(rx, "da dim lam vic: tren tau ca", re.IGNORECASE)
    assert re.search(rx, "dia diem lam viec: tren tau ca", re.IGNORECASE)
    assert not re.search(rx, "thoi gio lam viec", re.IGNORECASE)


def test_text_value_filter_rejects_money_and_junk():
    from app.domain.documents.rules import _is_junk_text_value
    assert _is_junk_text_value("0 VND")
    assert _is_junk_text_value("1.200 USD/thang")
    assert _is_junk_text_value("Tổng cộng: 0 tàu")
    assert _is_junk_text_value("   ")
    assert not _is_junk_text_value("Trên tàu cá: Captain Alex")
    assert not _is_junk_text_value("48 giờ/tuần")


def test_merge_llm_rejects_english_and_unwraps_object():
    """Model trả object + đơn vị tiếng Anh cho trường văn bản -> loại (giữ tiếng Việt)."""
    text = "Thi gi nghi ngoi: 90 phut/ngay"
    key = "thoi_gio_nghi_ngoi"
    out = merge_llm_extraction(
        _text_field_contract(key, text),
        {key: {"value": {"amount": 90, "currency": "Minutes", "period": "Days",
                         "raw": "90 minutes/day"},
               "evidence_quote": "Thi gi nghi ngoi: 90 phut/ngay", "confidence": 0.9}},
    )
    assert out["extracted_fields"][key]["value"] is None

    ok = merge_llm_extraction(
        _text_field_contract(key, text),
        {key: {"value": {"raw": "90 phút/ngày"},
               "evidence_quote": "Thi gi nghi ngoi: 90 phut/ngay", "confidence": 0.9}},
    )
    assert ok["extracted_fields"][key]["value"] == "90 phút/ngày"


def test_normalize_worktime_all_formats():
    from app.domain.documents.rules import normalize_worktime
    assert normalize_worktime(
        "7 gi 30 phút/ngày, 40 giò/tun, 173 gi 45 phút/tháng, 278 ngày/năm, 2080 gi/năm"
    ) == "7 giờ 30 phút/ngày; 40 giờ/tuần; 173 giờ 45 phút/tháng; 278 ngày/năm; 2080 giờ/năm"
    assert normalize_worktime("Thuyn vien lam 48 gio/tun") == "48 giờ/tuần"
    assert normalize_worktime("90 phút/ngày") == "90 phút/ngày"
    assert normalize_worktime("Theo tinh cht cong vic") is None


# ---------------------------------------------------------------------------
# GOLDEN — TRÍCH XUẤT: hợp đồng gán nhãn (Nhật Bản) — đo end-to-end tầng regex
# ---------------------------------------------------------------------------
def test_golden_extraction_nhat_ban_labeled_contract():
    from app.domain.documents.rules import extract_contract_json
    text = normalize_text(
        "VĂN BẢN ĐĂNG KÝ HỢP ĐỒNG CUNG ỨNG LAO ĐỘNG\n"
        "Số công văn: 123/CV-ABC\n"
        "Ngày ký hợp đồng: 11/12/2025\n"
        "Tổng số lao động: 120 người. Số lao động nữ: 45 người.\n"
        "Tiền lương: 200.000 JPY/tháng\n"
        "Tiền làm thêm giờ: 1.500 JPY/giờ\n"
        "Địa điểm làm việc: Osaka, Nhật Bản\n"
        "Thời giờ làm việc: 8 giờ/ngày, 40 giờ/tuần\n"
    )
    contract, _warn = extract_contract_json("s1", "f.pdf", text, text, "nhat_ban")
    ef = contract["extracted_fields"]
    golden = {
        "so_cong_van": "123/CV-ABC",
        "ngay_ky_hop_dong": "2025-12-11",       # dd/mm/yyyy -> ISO
        "tong_so_lao_dong": 120,
        "so_lao_dong_nu": 45,
        "dia_diem_lam_viec": "Osaka, Nhật Bản",
        "thoi_gio_lam_viec": "8 giờ/ngày; 40 giờ/tuần",
    }
    for k, want in golden.items():
        assert ef[k]["value"] == want, f"{k}: {ef[k]['value']!r} != {want!r}"
    assert ef["tien_luong"]["value"]["amount"] == 200000
    assert ef["tien_luong"]["value"]["currency"] == "JPY"
    assert ef["tien_lam_them_gio"]["value"]["amount"] == 1500
    # Trường không có trong văn bản phải nằm trong missing_fields (không bịa).
    assert "ky_quy_vnd" in contract["missing_fields"]


def test_clean_value_rejects_template_fill_hints():
    # Chữ HƯỚNG DẪN ĐIỀN của biểu mẫu không phải giá trị khai báo -> None.
    for junk in ("Điền theo thực tế đăng ký", "Điền 0", "Ghi theo thực tế",
                 "Nêu rõ số lượng", "(nếu có)", "Kê khai đầy đủ"):
        assert clean_value(junk) is None, junk
    # Giá trị thật không bị vạ lây.
    for ok in ("Không áp dụng biện pháp bảo lãnh", "Ghi chú: trả theo giờ", "Osaka, Nhật Bản"):
        assert clean_value(ok) == ok


# ---------------------------------------------------------------------------
# GOLDEN — BỘ TRƯỜNG 3 TẦNG: khu vực -> quốc gia -> công việc
# ---------------------------------------------------------------------------
def test_job_prompt_layers_region_country_work():
    """Tầng quốc gia CHỈ ghi phần khác; ghép với tầng khu vực phải ra bộ ĐỦ trường."""
    import json

    from app.store import COUNTRIES_DIR, load_job_prompt, region_of, resolve_job_prompt

    # 1) File tầng quốc gia là bản DIFF (ít trường hơn bộ đủ) — đó là mục đích tách tầng.
    country_raw = json.loads((COUNTRIES_DIR / "nhat_ban.json").read_text(encoding="utf-8"))
    full = resolve_job_prompt("nhat_ban", "nhat_ban", "", "nhat_ban")
    assert len(country_raw["fields_catalog"]) < len(full["fields_catalog"])
    assert len(full["fields_catalog"]) >= 43

    # 2) Khóa đơn (đường cũ) vẫn phải ra bộ ĐỦ — không được trả bản diff cụt.
    assert load_job_prompt("nhat_ban")["fields_catalog"].keys() == full["fields_catalog"].keys()

    # 3) Khu vực suy từ ĐÚNG quốc gia đã chọn (thị trường trải nhiều khu vực).
    assert region_of("nhat_ban", "nhat_ban") == "dong_bac_a"
    assert region_of("tay_a_trung_a_chau_phi", "qatar") == "tay_a"
    assert region_of("tay_a_trung_a_chau_phi", "angola") == "chau_phi"

    # 4) BIỂN QUỐC TẾ là TẦNG CÔNG VIỆC: chồng lên được mọi khu vực, không phải thị trường.
    # Biển quốc tế KHÔNG còn là khu vực/thị trường — chỉ còn là loại hình lao động.
    sea_only = resolve_job_prompt("", "", "cong_viec_tren_bien", "")
    jp_sea = resolve_job_prompt("nhat_ban", "nhat_ban", "cong_viec_tren_bien", "nhat_ban")
    assert "Công việc trên biển" in sea_only["display_name"]
    assert jp_sea["display_name"] == sea_only["display_name"]      # tầng công việc thắng
    # Trường riêng của Nhật mà tầng công việc KHÔNG đụng tới thì vẫn giữ nguyên,
    # còn trường tầng công việc CÓ khai thì tầng công việc thắng.
    assert "cac_khoan_khau_tru" not in json.loads(
        (COUNTRIES_DIR / "works" / "cong_viec_tren_bien.json").read_text(encoding="utf-8")
    )["fields_catalog"]
    assert (jp_sea["fields_catalog"]["cac_khoan_khau_tru"]
            == full["fields_catalog"]["cac_khoan_khau_tru"])
    # Tầng công việc cũng là bản DIFF: nó chỉ khai khóa con KHÁC (tiêu chí, gợi ý nhập,
    # loại kiểm tra), còn nhãn và nhóm hiển thị thừa hưởng từ tầng dưới. Nên phép so
    # đúng là "khóa nào tầng công việc CÓ khai thì thắng", không phải "hai bên bằng nhau".
    sea_diff = sea_only["fields_catalog"]["dia_diem_lam_viec"]
    assert set(sea_diff) < set(jp_sea["fields_catalog"]["dia_diem_lam_viec"])
    for k, v in sea_diff.items():
        assert jp_sea["fields_catalog"]["dia_diem_lam_viec"][k] == v
    assert jp_sea["fields_catalog"]["dia_diem_lam_viec"]["label"] == "Địa điểm làm việc"


def test_sea_work_config_keys_follow_job_type():
    """Biển quốc tế thôi làm THỊ TRƯỜNG -> mọi bảng tra phải chuyển sang khóa LOẠI HÌNH."""
    from app.domain.compliance.factual import check_deposit
    from app.domain.documents.spelling import phrases_for_job
    from app.store import load_dossier_rules, load_input_quality_config, load_markets

    mk = load_markets()
    # 1) Không còn khu vực/thị trường "biển quốc tế"; mọi thị trường đều có loại hình đó.
    assert not any(r["id"] == "bien_quoc_te" for r in mk["regions"])
    assert not any(m["id"] == "bien_quoc_te" for m in mk["markets"])
    assert all(any(j["id"] == "cong_viec_tren_bien" for j in m["job_types"]) for m in mk["markets"])

    # 2) Ký quỹ: tra theo LOẠI HÌNH trước, rồi quốc gia, rồi thị trường.
    #    Phụ lục II NĐ 112/2021: thuyền viên tàu cá xa bờ/tàu vận tải KHÔNG ký quỹ ở
    #    MỌI thị trường -> loại hình phải thắng quy tắc thị trường.
    pol = mk["deposit_policy"]
    assert "cong_viec_tren_bien" in pol["by_job_type"]
    sea = check_deposit(50_000_000, "han_quoc", pol, "cong_viec_tren_bien")
    assert sea and sea[0] == "FAIL"
    assert check_deposit(0, "han_quoc", pol, "cong_viec_tren_bien")[0] == "PASS"
    # Quốc gia thắng thị trường: Qatar (Trung Đông) không ký quỹ dù thị trường gộp cho phép.
    assert check_deposit(9_000_000, "tay_a_trung_a_chau_phi", pol, "xay_dung", "qatar")[0] == "FAIL"

    # 3) Đơn vị tiền lương + thành phần hồ sơ + cụm đáp án cũng theo loại hình.
    assert load_input_quality_config()["expected_currency"]["cong_viec_tren_bien"] == ["USD"]
    assert "uy_quyen_chu_tau" in load_dossier_rules()["required_components"]["by_market"]["cong_viec_tren_bien"]
    assert phrases_for_job("dong_nam_a", "cong_viec_tren_bien").get("dia_diem_lam_viec")


def test_selection_carries_region_name():
    """Ô đầu trang 2/3 hiện KHU VỰC — thị trường một nước (Nhật Bản) làm ô 'Thị
    trường' trùng luôn ô Quốc gia, không nói thêm được gì."""
    from app.domain.documents.intake import _region_of

    # Tên khu vực nay SONG NGỮ "English (Tiếng Việt)" — cùng lối viết đã dùng cho
    # quốc gia ("Japan (Nhật Bản)"), để một danh mục đọc được bằng cả hai thứ tiếng
    # mà KHÔNG phải dịch dữ liệu ở tầng giao diện. Test khóa phần tiếng Việt (phần
    # mang nghĩa pháp lý) và chỉ yêu cầu có phần tiếng Anh đứng trước.
    rid, rname = _region_of("nhat_ban", "nhat_ban")
    assert rid == "dong_bac_a"
    assert rname.endswith("(Đông Bắc Á)") and rname != "(Đông Bắc Á)"
    # Thị trường gộp: khu vực suy từ ĐÚNG quốc gia đã chọn, không phải quốc gia đầu tiên.
    assert _region_of("tay_a_trung_a_chau_phi", "qatar")[0] == "tay_a"
    assert "Châu Phi" in _region_of("tay_a_trung_a_chau_phi", "angola")[1]
    assert _region_of("", "") == ("", "")


def test_ocr_tolerant_label_covers_substitution_errors():
    """Nhãn chịu lỗi OCR phải phủ CẢ HAI kiểu hỏng, không chỉ kiểu rụng ký tự.

    Hai kiểu OCR hỏng khác nhau: loại RỤNG nguyên âm có dấu ('lương' ->
    'lng'), engine khác THAY nó bằng chữ gần giống hoặc làm nó nở ra 2 ký tự
    ('lương' -> 'lvong'/'wong', 'sức' -> 'strc', 'từ' -> 'tir'). Nhãn chỉ chịu
    được kiểu thứ nhất thì gặp kiểu thứ hai là trượt sạch — và trượt nhãn thì rule
    im lặng trả rỗng, không lỗi nào được ghi ra."""
    import re

    from app.domain.documents.ocr import fold_diacritics
    from app.domain.documents.rules.parse import _ocr_tolerant_label_regex

    def hit(label: str, line: str) -> bool:
        rx = _ocr_tolerant_label_regex(label, anchor=True)
        return bool(rx and re.search(rx, "\n" + fold_diacritics(line), re.IGNORECASE))

    # PHẢI bắt được — cả bản rụng ký tự lẫn bản thay ký tự của cùng một nhãn.
    assert hit("Tiền lương", "- Tin luong: 550 USD")
    assert hit("Tiền lương", "- Tién lvong/tién cong: 184.461 JPY")
    assert hit("Địa điểm làm việc", "- Đa dim lam vic: Tren cac tau")
    assert hit("Địa điểm làm việc", "- Dia diém lam viéc: Kanagawa-Ken")
    assert hit("Chi phí người lao động phải trả", "4. Chi phi nguoi lao dng phi tra")
    assert hit("Chi phí người lao động phải trả", "4. Chi phi ngudi lao déng phai tra")
    assert hit("Các khoản khấu trừ từ lương", "- Cac khon khu tr t luong theo quy dnh")
    assert hit("Các khoản khấu trừ từ lương", "- Cac khoan khdu trir tir wong theo quy dinh")
    assert hit("Khám sức khỏe", "+ Kham strc khee: 1.400.000 VND")
    assert hit("Ký quỹ", "- Ký qu: Không có")

    # KHÔNG được khớp bừa — nới nhãn mà mất tính phân biệt thì còn tệ hơn trượt.
    assert not hit("Tiền lương", "- Thoi gian tuyen chon: 3 thang")
    assert not hit("Tiền lương", "- Cong uoc Lao dong Hang hai 2006")
    assert not hit("Ký quỹ", "- Khong qua 12 thang ke tu ngay chap thuan")
    assert not hit("Bảo lãnh", "- Bao hiem xa hoi (dong cho co quan BHXH): 0 VND")
    assert not hit("Số lao động nữ", "- Nganh, nghe: Lao dong dac dinh - San xuat may")
    assert not hit("Địa điểm làm việc", "- Thoi gio lam viec: 6.55 gio/ngay")


# ---------------------------------------------------------------------------
# GOLDEN — hồ sơ THẬT NHẬT HUY KHANG × IM KYODO KUMIAI (04/11/2025), thị trường Nhật Bản
# Chốt các lỗi trích xuất đã báo: Số công văn ≠ Mã hồ sơ, ngày công văn (ngày CHỮ bên
# phải Số), MST từ chữ ký số, thời hạn HĐ, ATVSLĐ không nuốt trường kế.
# ---------------------------------------------------------------------------
def _dang_ky_text():
    from app.domain.documents.rules import normalize_text
    return normalize_text(
        "Mã hồ sơ: 854156\nCÔNG TY CỔ PHẦN NHẬT HUY KHANG INTERNATIONAL\nMST: 0316075160\n"
        "Số: 114/NHHK-2025      Ngày 04 tháng 11 năm 2025\n"
        "ĐĂNG KÝ HỢP ĐỒNG CUNG ỨNG LAO ĐỘNG\nKính gửi: Cục Quản lý Lao động Ngoài nước\n"
        "1. Tên doanh nghiệp: CÔNG TY CỔ PHẦN NHẬT HUY KHANG INTERNATIONAL\n"
        "2. Doanh nghiệp đăng ký Hợp đồng cung ứng lao động đi làm việc tại Nhật Bản kí ngày 01/07/2025\n"
        "- Thời hạn hợp đồng lao động: 5 năm\n- Số lượng: 1, trong đó nữ: 0\n3. Nội dung:\n"
        "- Ngành, nghề: Lao động đặc định - Sản xuất máy công nghiệp, trong đó số có nghề: 0\n"
        "- Địa điểm làm việc: Kanagawa-Ken, Minami Ashigara-Shi, Mamashita 350\n"
        "- An toàn, vệ sinh lao động: Người sử dụng lao động phải tổ chức huấn luyện an toàn, "
        "vệ sinh lao động; cung cấp miễn phí, đầy đủ trang thiết bị làm việc, dụng cụ bảo hộ "
        "lao động phù hợp với ngành, nghề, công việc của người lao động; đảm bảo nơi làm việc "
        "an toàn và vệ sinh lao động\n"
        "- Tiền lương/tiền công: 184.461 JPY/tháng (1.275 JPY/giờ)\n"
        "5. Các thỏa thuận khác:\n- Ký quỹ: Không có\n"
        "6. Thời gian tuyển chọn: 3 tháng (không quá 12 tháng kể từ ngày chấp thuận đăng ký hợp đồng cung ứng lao động)\n"
        "7. Thời gian dự kiến xuất cảnh: tháng 02/2026\n")


def test_golden_dang_ky_nhat_ban_real():
    from app.domain.documents.rules import extract_contract_json
    dk = _dang_ky_text()
    ef = extract_contract_json("s", "van ban dang ky hop dong.pdf", dk, dk, "nhat_ban")[0]["extracted_fields"]
    golden = {
        "so_cong_van": "114/NHHK-2025",        # KHÔNG lấy 'Mã hồ sơ: 854156'
        "ngay_cong_van": "2025-11-04",          # ngày CHỮ nằm bên phải Số công văn
        "tong_so_lao_dong": 1,
        "so_lao_dong_nu": 0,
        "so_lao_dong_co_nghe": 0,
        "thoi_han_hop_dong": "5 năm",           # trường MỚI
        "thoi_gian_tuyen_chon_thang": 3,
        "thoi_gian_du_kien_xuat_canh": "2026-02-01",
        "dia_diem_lam_viec": "Kanagawa-Ken, Minami Ashigara-Shi, Mamashita 350",
    }
    for k, want in golden.items():
        assert ef[k]["value"] == want, f"{k}: {ef[k]['value']!r} != {want!r}"
    # ATVSLĐ giữ trọn câu, KHÔNG nuốt sang 'Tiền lương' và KHÔNG bị cắt ở 'ngành' trong câu.
    atv = ef["an_toan_ve_sinh_lao_dong"]["value"]
    assert atv.endswith("an toàn và vệ sinh lao động")
    assert "JPY" not in atv and "184.461" not in atv


def test_merge_prefers_canonical_source_doc():
    """#2 Bộ lọc nguồn: Số công văn lấy từ VĂN BẢN ĐĂNG KÝ (không nhầm Số hợp đồng cung ứng)."""
    from app.domain.compliance.reconcile import merge_contracts
    cung_ung = {
        "contract_meta": {"source_file": "2. ban sao hop dong cung ung.pdf", "market_id": "nhat_ban"},
        "raw": {"ocr_text": "HỢP ĐỒNG CUNG ỨNG LAO ĐỘNG"},
        "extracted_fields": {
            "so_cong_van": {"value": "07/2025/HĐKNĐĐ"},          # SỐ HỢP ĐỒNG (sai vai)
        }, "missing_fields": []}
    dang_ky = {
        "contract_meta": {"source_file": "1. van ban dang ky hop dong.pdf", "market_id": "nhat_ban"},
        "raw": {"ocr_text": "ĐĂNG KÝ HỢP ĐỒNG CUNG ỨNG Kính gửi: Cục Quản lý Lao động Ngoài nước"},
        "extracted_fields": {
            "so_cong_van": {"value": "114/NHHK-2025"},
        }, "missing_fields": []}
    ef = merge_contracts([cung_ung, dang_ky])["extracted_fields"]
    assert ef["so_cong_van"]["value"] == "114/NHHK-2025"


def test_rag_market_tag_isolates_other_markets():
    """#1 Điều khoản riêng của Hàn Quốc (E10) KHÔNG lọt vào truy vấn hồ sơ Đài Loan."""
    from app.domain.regulations import _where_clause, market_of_chunk
    korea = ("Thuyền viên tàu cá gần bờ (thị thực E10). Đối với nội dung điểm b, d khoản 2 "
             "Điều 19, thỏa thuận phù hợp với quy định pháp luật Hàn Quốc.")
    taiwan = "Các ngành, nghề phù hợp với Luật Lao động cơ bản của Đài Loan (Trung Quốc)."
    general = "Điều 19. Hợp đồng cung ứng lao động phải phù hợp pháp luật Việt Nam."
    assert market_of_chunk(korea) == "han_quoc"
    assert market_of_chunk(taiwan) == "dai_loan"
    assert market_of_chunk(general) == "chung"
    conds = _where_clause("2025-11-04", "VN", ["circular"], "dai_loan")["$and"]
    assert {"market": {"$in": ["dai_loan", "chung"]}} in conds




# ---------------------------------------------------------------------------
# HỒ SƠ THẬT VJEC × Megumi (12/08/2025) — bản scan rụng dấu nặng.
# Mỗi test dưới đây khóa lại MỘT lối hỏng đã từng cho ra giá trị sai/trống.
# ---------------------------------------------------------------------------
_VJEC_DANG_KY = (
    "DĂNG KÝ HP DÒNG CUNG ÚNG LAO ĐNG\n"
    "2. Doanh nghip đăng ký Hp đng cung ng lao đng đi làm vic ti Nht Bn ki\n"
    "ngày 12/08/2025 vi bn nưc ngoài tip nhn lao đng\n"
    "- Tin lưong/tièn công: 193.200 JPY/tháng (1.150 JPY/ giò)\n"
    "- Các khon khu t t lưong theo quy đnh ca nưc tip nhn lao đng: Thu :\n"
    "9.349 yên/ tháng; Bo him xã hi, bo him vic làm : 10.460 yên/ tháng; Tin đin\n"
    "nưc ga : Thc chi\n"
    "4. Chi phí ngưi lao đng phi trå trưc khi đi:\n"
    "- Chi phí khác:\n"
    "+ Bồi dưõng k năng ngh, ngoi ngũ (nu có): 0 VNĐ\n"
    "+ Chi phí khác: 200.000 VNĐ (Phí trà cho đi lý làm visa)\n"
    "- Tồng cng: 4.200.000 VNĐ\n"
)


def _vjec_fields():
    from app.domain.documents.rules import extract_contract_json
    cj, _ = extract_contract_json("t", "1. van ban dang ky.pdf", "", _VJEC_DANG_KY, "nhat_ban")
    return cj["extracted_fields"]


def test_money_label_anchored_no_longer_loses_value():
    """Nhãn tiền NEO ĐẦU DÒNG: cửa sổ phải tính từ SAU nhãn.

    Cụm khớp mở đầu bằng '\\n- ' (do neo), nên cắt-tại-mục-kế trên cả cụm cho ra mảnh
    RỖNG -> mọi nhãn neo im lặng trượt số tiền, 'Tiền lương' phải nhờ LLM và ra 1.932."""
    v = _vjec_fields()["tien_luong"]["value"]
    assert v["amount"] == 193200 and v["currency"] == "JPY" and v["period"] == "tháng"


def test_money_note_keeps_parenthetical():
    """Ghi chú trong ngoặc là MỘT PHẦN của giá trị: mức quy đổi thứ hai / lý do khoản thu."""
    ef = _vjec_fields()
    assert "1.150" in (ef["tien_luong"]["value"].get("note") or "")
    assert "visa" in (ef["chi_phi_khac_nld_nop"]["value"].get("note") or "")


def test_heading_label_does_not_steal_next_line_amount():
    """'- Chi phí khác:' là TIÊU ĐỀ danh sách con (không có số). Nối dòng vô điều kiện
    khiến nó nuốt '0 VNĐ' của dòng dưới; phải đi tiếp tới '+ Chi phí khác: 200.000'."""
    assert _vjec_fields()["chi_phi_khac_nld_nop"]["value"]["amount"] == 200000


def test_label_tail_is_not_returned_as_value():
    """Nhãn dài bị OCR ăn mất phụ âm ('khấu trừ từ' -> 'khu t t'): phần ĐUÔI NHÃN còn
    lại từng bị trả về làm giá trị ('Theo quy định của nước tiếp nhận lao động')."""
    v = _vjec_fields()["cac_khoan_khau_tru"]["value"]
    assert "9.349" in v and "10.460" in v
    assert not v.lower().startswith("theo quy")


def test_date_label_scans_all_occurrences():
    """Nhãn 'đăng ký Hợp đồng cung ứng lao động' trúng TIÊU ĐỀ (không có ngày) trước;
    phải duyệt tiếp tới mục 2 mới thấy '12/08/2025'."""
    assert _vjec_fields()["ngay_ky_hop_dong"]["value"] == "2025-08-12"


def test_no_fabricated_date_from_worktime_text():
    """dateutil(fuzzy=True) từng BỊA ra ngày từ '8 giờ/ngày, 40 giờ/tuần'."""
    from app.domain.documents.rules import _try_parse_date_any
    assert _try_parse_date_any("Thi gian làm vi: Không quá 8 gi/ngày, 40 gi/tun") is None


def test_duration_shape_rejects_reference_clause():
    """'Thời hạn hợp đồng' có KHUÔN: câu dẫn chiếu 'cụ thể trong Thư yêu cầu…' phải bị
    loại, còn bảng đảo cột ('5 nǎm' đứng TRƯỚC nhãn 'Thời hạn làm việc') phải bắt được."""
    from app.domain.documents.rules import extract_contract_json
    ref = ("- S lưng, ngành ngh, công vic, lưong, đja dim làm vic, thòi han hp đng: "
           "cu th trong Thư yêu cu tuyn dng lao đng;\n")
    table = "5 nǎm( ）\nLoai Visa\nKý nang dac đinh só 1\nThi han lám viec\n"
    assert extract_contract_json("t", "f", "", ref, "nhat_ban")[0][
        "extracted_fields"]["thoi_han_hop_dong"]["value"] is None
    assert extract_contract_json("t", "f", "", table, "nhat_ban")[0][
        "extracted_fields"]["thoi_han_hop_dong"]["value"] == "5 năm"


def test_fee_alert_needs_an_amount_on_the_line():
    """Pattern viết KHÔNG DẤU + bản scan rụng dấu: 'phải có' hóa thành 'phí cò'. Khoản
    thu thì luôn kèm SỐ TIỀN; câu điều khoản thì không."""
    from app.domain.compliance.quality import _prohibited_fee_flags
    from app.store import load_input_quality_config
    cfg = load_input_quality_config()
    dieu_khoan = "Công ty s dng lao đng phi có trách nhim b tri nhà đm bo an toàn v sinh cho"
    assert _prohibited_fee_flags(dieu_khoan, cfg) == []
    khoan_thu = "+ Chi phí khác: 200.000 VNĐ (Phí trà cho đi lý làm visa)"
    flags = _prohibited_fee_flags(khoan_thu, cfg)
    assert [f["code"] for f in flags] == ["PROHIBITED_FEE"]
    assert "Phí trả" in flags[0]["snippet"]      # trích đoạn đã khôi phục dấu


# ---------------------------------------------------------------------------
# ĐIỀU KHOẢN DÀI — giữ câu có nội dung thay vì cắt cụt, và không dừng ở câu dẫn chiếu
# ---------------------------------------------------------------------------
_AN_O = (
    "Người sử dụng lao động cung cấp chỗ ở miễn phí cho người lao động. "
    "Hai bên có thể thỏa thuận thêm bằng văn bản riêng nếu thấy cần thiết. "
    "Tiền điện, tiền nước do người lao động tự chi trả theo thực tế sử dụng. "
    "Bữa ăn ca được công ty hỗ trợ 50% chi phí."
)


def test_doan_qua_dai_giu_tron_cau_co_tu_khoa():
    """Cắt cứng ở ký tự thứ N làm giá trị đứt giữa từ và thường mất đúng phần nội
    dung. Giữ NGUYÊN VĂN từng câu, ưu tiên câu chứa từ khóa của trường."""
    from app.domain.documents.rules import _condense_clause
    out = _condense_clause(_AN_O, ["cho o", "dien", "nuoc", "bua an"], 150)
    assert len(out) <= 150
    assert out.endswith(("dụng", "phí")), f"không được đứt giữa từ: {out!r}"
    assert "chỗ ở miễn phí" in out and "Tiền điện, tiền nước" in out
    assert "thỏa thuận thêm bằng văn bản riêng" not in out, "câu không liên quan phải rụng"


def test_doan_ngan_giu_nguyen_van():
    from app.domain.documents.rules import _condense_clause
    ngan = "Chỗ ở do công ty bố trí, miễn phí."
    assert _condense_clause(ngan, ["cho o"], 300) == ngan


def test_khong_tu_khoa_nao_khop_thi_giu_cac_cau_dau():
    from app.domain.documents.rules import _condense_clause
    out = _condense_clause(_AN_O, ["khong-co-tu-nay"], 120)
    assert out.startswith("Người sử dụng lao động cung cấp chỗ ở miễn phí")
    assert len(out) <= 120


def test_mot_cau_dai_hon_gioi_han_van_cat_o_ranh_gioi_tu():
    from app.domain.documents.rules import _condense_clause
    out = _condense_clause("Người lao động " + "rất " * 60 + "cẩn thận", [], 50)
    assert len(out) <= 50 and not out.endswith("r")


def test_nhan_dien_cau_dan_chieu_chung():
    """'Theo quy định của pháp luật Nhật Bản' đúng hình thức nhưng rỗng nội dung —
    phải xếp SAU mọi đoạn cụ thể hơn, chứ không phải bị loại bỏ."""
    from app.domain.documents.rules import is_boilerplate_clause
    for chung in ("Theo quy định của pháp luật Nhật Bản",
                  "Thực hiện theo quy định của pháp luật Việt Nam và Nhật Bản",
                  "Theo luật hiện hành",
                  "Áp dụng theo chính sách của công ty"):
        assert is_boilerplate_clause(chung), chung
    for cu_the in ("Người lao động được tham gia bảo hiểm y tế, bảo hiểm tai nạn lao động",
                   "Công ty chi trả vé máy bay lượt đi, người lao động tự lo lượt về",
                   "Theo quy định của pháp luật Nhật Bản, người lao động được hưởng "
                   "chế độ bảo hiểm y tế và bảo hiểm hưu trí do chủ sử dụng đóng góp"):
        assert not is_boilerplate_clause(cu_the), cu_the


def test_uu_tien_doan_cu_the_hon_cau_dan_chieu():
    """Nhãn xuất hiện HAI chỗ: chỗ đầu chỉ ghi câu dẫn chiếu, chỗ sau ghi nội dung
    thật. Nhánh nào khớp trước cũng không được chốt luôn nếu nó rỗng nội dung."""
    from app.domain.documents.rules import extract_contract_json
    text = ("- Các chế độ bảo hiểm: Theo quy định của pháp luật Nhật Bản\n"
            "\n"
            "Các chế độ bảo hiểm: Người lao động tham gia bảo hiểm y tế, "
            "bảo hiểm hưu trí và bảo hiểm tai nạn lao động.\n")
    got = extract_contract_json("t", "f", "", text, "nhat_ban")[0][
        "extracted_fields"]["cac_che_do_bao_hiem"]["value"]
    assert got and "bảo hiểm y tế" in got


def test_chi_co_cau_dan_chieu_thi_van_lay_lam_gia_tri():
    """Xếp sau KHÁC với loại bỏ: hồ sơ chỉ ghi đúng câu đó thì nó vẫn là giá trị thật,
    để trống mới là sai (ô trống ra NEEDS_SUPPLEMENT nhầm)."""
    from app.domain.documents.rules import extract_contract_json
    text = "- Các chế độ bảo hiểm: Theo quy định của pháp luật Nhật Bản\n"
    got = extract_contract_json("t", "f", "", text, "nhat_ban")[0][
        "extracted_fields"]["cac_che_do_bao_hiem"]["value"]
    assert got and "pháp luật Nhật Bản" in got


def test_moi_truong_dieu_khoan_dai_deu_co_tu_khoa():
    """10 trường điều khoản dài PHẢI khai `keywords`, nếu không đoạn quá dài lại quay
    về cắt cụt mà không có gì báo."""
    from app.store import load_extraction_config
    ltr = load_extraction_config()["labeled_text_rules"]
    can_co = ["an_toan_ve_sinh_lao_dong", "cac_khoan_khau_tru", "dieu_kien_an_o_sinh_hoat",
              "cac_che_do_bao_hiem", "cham_dut_truoc_han_va_boi_thuong",
              "trach_nhiem_khi_gap_rui_ro", "trach_nhiem_xu_ly_phat_sinh",
              "giai_quyet_tranh_chap_luat_ap_dung", "ve_may_bay_text",
              "che_do_kham_chua_benh_suc_khoe_sinh_san"]
    thieu = [k for k in can_co if not (ltr.get(k) or {}).get("keywords")]
    assert not thieu, f"thiếu keywords: {thieu}"


# ---------------------------------------------------------------------------
# NGÀY KÝ do người duyệt NHẬP TAY -> ISO
# ---------------------------------------------------------------------------
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
