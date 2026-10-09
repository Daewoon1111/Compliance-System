"""Test kiểm tra TẤT ĐỊNH + cờ chất lượng + bất biến của bộ trường (code thuần, không LLM)."""
from datetime import date

import pytest

from app.domain.compliance.factual import (
    DETERMINISTIC_TYPES,
    check_positive_integer,
    run_deterministic_check,
)
from app.domain.compliance.quality import (
    _signed_date_flags,
    blocking_fields,
    signed_date_field_of,
    signed_date_of,
    trusted_derived_date,
)
from app.store import field_set_problems, load_field_set


# ---------------------------------------------------------------------------
# Kiểm tra tất định: chỉ còn `positive_integer`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("value", "verdict"), [
    (50, "PASS"),
    ("50 bản", "PASS"),            # chuỗi có số -> đọc ra số
    ("1.500", "PASS"),             # dấu phân cách nghìn
    (3.0, "PASS"),                 # số thực nhưng nguyên
    ({"amount": 7}, "PASS"),       # object số lượng/tiền
    (0, "FAIL"),
    (-3, "FAIL"),
    (2.5, "FAIL"),                 # không nguyên
    ("không rõ", "FAIL"),
    (True, "FAIL"),                # bool KHÔNG phải số nguyên
    (None, "NEEDS_SUPPLEMENT"),
    ("   ", "NEEDS_SUPPLEMENT"),
])
def test_positive_integer(value, verdict):
    assert check_positive_integer(value)[0] == verdict
    assert run_deterministic_check("positive_integer", value)[0] == verdict


def test_chi_positive_integer_la_tat_dinh():
    assert DETERMINISTIC_TYPES == ("positive_integer",)
    assert run_deterministic_check("khong_ton_tai", 1) is None
    assert run_deterministic_check("regulated", 1) is None
    assert run_deterministic_check("declaration", 1) is None


def test_nguong_toi_thieu_doc_tu_checks_json(monkeypatch):
    """Tham số nằm ở `checks.json > factual`, không cứng trong mã."""
    import app.domain.compliance.factual as factual
    monkeypatch.setattr(factual, "load_factual_rules", lambda: {"positive_integer": {"min": 10}})
    assert check_positive_integer(9)[0] == "FAIL"
    assert check_positive_integer(10)[0] == "PASS"


# ---------------------------------------------------------------------------
# Cờ chất lượng chặn kết luận
# ---------------------------------------------------------------------------
def test_blocking_fields_filters_block_only():
    flags = [
        {"code": "A", "field": "gia_tri", "block_field": True, "message": "x"},
        {"code": "B", "field": "khac", "block_field": False},
        {"code": "C", "field": "gia_tri", "block_field": True},  # trùng field -> giữ flag đầu
        {"code": "D", "field": None, "block_field": True},       # không gắn trường -> bỏ
    ]
    out = blocking_fields(flags)
    assert set(out) == {"gia_tri"} and out["gia_tri"]["code"] == "A"
    assert blocking_fields(None) == {}


# ---------------------------------------------------------------------------
# NGÀY KÝ — chỉ khi bộ trường khai `signed_date_field`
# ---------------------------------------------------------------------------
def _hop_dong(**kw) -> dict:
    return {"contract_meta": {"signed_date_field": "ngay_ky"}, **kw}


def test_ngay_ky_doc_theo_dung_mot_thu_tu():
    """Thứ tự: trường ngày ký (người duyệt sửa được) -> derived đủ tin -> ''."""
    c = _hop_dong(extracted_fields={"ngay_ky": {"value": "2025-03-01"}},
                  derived={"signed_date": {"value": "2024-01-01", "confidence": 0.9}})
    assert signed_date_field_of(c) == "ngay_ky"
    assert signed_date_of(c) == "2025-03-01"
    c["extracted_fields"]["ngay_ky"]["value"] = None
    assert signed_date_of(c) == "2024-01-01"
    assert signed_date_of(_hop_dong()) == "", "không có ngày ký -> chuỗi RỖNG, không bịa mốc"


def test_bo_truong_khong_khai_ngay_ky_thi_khong_doc_ngay_ky():
    """Không có `signed_date_field` -> không lọc quy định theo hiệu lực, kể cả khi
    phần suy ra có một ngày nào đó."""
    c = {"extracted_fields": {"ngay_ky": {"value": "2025-03-01"}},
         "derived": {"signed_date": {"value": "2024-01-01", "confidence": 0.9}}}
    assert signed_date_field_of(c) == "" and signed_date_of(c) == ""


def test_ngay_suy_ra_do_tin_thap_khong_lam_moc():
    """Ngày đầu tiên xuất hiện trong văn bản thường là ngày của văn bản được dẫn chiếu."""
    assert trusted_derived_date({"derived": {"signed_date": {"value": "2020-01-01",
                                                             "confidence": 0.25}}}) == ""
    assert trusted_derived_date({"derived": {"signed_date": {
        "value": "2020-01-01", "confidence": 0.25, "from_field": True}}}) == "2020-01-01"
    assert trusted_derived_date({}) == ""


@pytest.mark.parametrize(("raw", "code"), [
    (None, "SIGNED_DATE_MISSING"),
    ("ba mươi tháng hai", "SIGNED_DATE_INVALID"),
    ("2026-12-31", "SIGNED_DATE_FUTURE"),
    ("1980-05-05", "SIGNED_DATE_TOO_OLD"),
])
def test_co_ngay_ky_bat_thuong(raw, code):
    cfg = {"signed_date": {"earliest": "1990-01-01", "allow_future_days": 2}}
    c = _hop_dong(extracted_fields={"ngay_ky": {"value": raw}})
    flags = _signed_date_flags(c, cfg, date(2026, 10, 7))
    assert [f["code"] for f in flags] == [code]
    assert flags[0]["field"] == "ngay_ky" and flags[0]["needs_signed_date"] is True


def test_ngay_ky_hop_le_hoac_khong_dung_ngay_ky_thi_khong_co_co():
    cfg = {"signed_date": {"earliest": "1990-01-01", "allow_future_days": 2}}
    ok = _hop_dong(extracted_fields={"ngay_ky": {"value": "2025-06-01"}})
    assert _signed_date_flags(ok, cfg, date(2026, 10, 7)) == []
    assert _signed_date_flags({"extracted_fields": {}}, cfg, date(2026, 10, 7)) == []


# ---------------------------------------------------------------------------
# BỘ TRƯỜNG mặc định
# ---------------------------------------------------------------------------
def test_bo_truong_mau_hop_le_va_tu_nhat_quan():
    fs = load_field_set("hop_dong_mau")
    assert fs["id"] == "hop_dong_mau" and fs["document_kind"]
    assert field_set_problems(fs) == []
    fc = fs["fields_catalog"]
    assert fc[fs["signed_date_field"]]["value_type"] == "date"
    for k in fs["field_check_mode"]["always_check"]:
        assert fc[k].get("check_type", "regulated") == "regulated", k
    # Có ít nhất một trường mỗi kiểu kiểm tra để luồng nào cũng chạy được trên bộ mẫu.
    assert {e.get("check_type", "regulated") for e in fc.values()} == {
        "regulated", "declaration", "positive_integer"}


def test_load_field_set_tra_ban_sao():
    """Nơi gọi lỡ sửa bộ trường không được làm hỏng bộ trường của mọi phiên sau."""
    a = load_field_set("hop_dong_mau")
    a["fields_catalog"].clear()
    assert load_field_set("hop_dong_mau")["fields_catalog"]


@pytest.mark.parametrize("bad_id", ["khong_ton_tai", "../services/checks", ""])
def test_bo_truong_khong_co_hoac_ma_doc_hai(bad_id):
    with pytest.raises(FileNotFoundError):
        load_field_set(bad_id)


def test_field_set_problems_bat_loi_cau_hinh():
    bad = {
        "display_name": "Mẫu",
        "signed_date_field": "so_hd",
        "fields_catalog": {
            "so_hd": {"label": "Số", "value_type": "text"},
            "gia": {"label": "Giá", "value_type": "tien", "check_type": "bat_ky"},
            "x y": {"label": ""},
        },
        "field_check_mode": {"always_check": ["khong_co"]},
    }
    msgs = " | ".join(field_set_problems(bad))
    for mau in ("value_type 'tien'", "check_type 'bat_ky'", "'x y'", "khong_co",
                "signed_date_field 'so_hd'"):
        assert mau in msgs, mau
    assert field_set_problems([]) == ["Nội dung phải là một đối tượng JSON."]


# ---------------------------------------------------------------------------
# DPI — nút duy nhất kéo theo số ô ảnh và số đoạn quy định gửi LLM
# ---------------------------------------------------------------------------
def test_tier_theo_dpi_dung_ba_bac():
    """Bất biến nằm ở CÁCH CHỌN BẬC, không ở mấy con số: bậc chọn theo `dpi < max_dpi`,
    bậc cuối bao trọn phần còn lại, và bậc cao hơn thì gửi nhiều đoạn quy định hơn."""
    from app.routers.meta import _DPI_TIERS_DEFAULTS, _ocr_cfg, dpi_bounds, tier_for_dpi
    assert dpi_bounds() == (120, 300)

    bang = sorted(_ocr_cfg().get("dpi_tiers") or _DPI_TIERS_DEFAULTS,
                  key=lambda t: int(t["max_dpi"]))
    tiles = [int(t["vintern_max_tiles"]) for t in bang]
    assert tiles == sorted(tiles) and len(set(tiles)) == 3, tiles
    caps = [int(t["rag_total_cap"]) for t in bang]
    assert caps == sorted(caps) and len(set(caps)) == 3, caps

    lo1, lo2 = int(bang[0]["max_dpi"]), int(bang[1]["max_dpi"])
    for dpi, bac in ((120, 0), (lo1 - 1, 0), (lo1, 1), (lo2 - 1, 1), (lo2, 2), (300, 2)):
        t = tier_for_dpi(dpi)
        assert t["vintern_max_tiles"] == bang[bac]["vintern_max_tiles"], dpi
        assert t["rag_total_cap"] == bang[bac]["rag_total_cap"], dpi
