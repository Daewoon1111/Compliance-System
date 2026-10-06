"""Hồi quy các lỗ hổng tìm thấy trong đợt audit 17/09/2026 (xem audit/BAO_CAO_AUDIT_*.md).

Mỗi test khóa lại hành vi ĐÃ SỬA; test đỏ nghĩa là lỗ hổng quay lại.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import core
from app.store import config as config_store


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(core.settings, "admin_token", "ma-quan-tri-test-0123456789")
    ucfg = tmp_path / "user_config"
    monkeypatch.setattr(config_store, "USER_CONFIG_DIR", ucfg)
    monkeypatch.setattr(config_store, "APPLIED_FILE", ucfg / "applied.json")
    for kind in list(config_store.USER_CONFIG_DIRS):
        monkeypatch.setitem(config_store.USER_CONFIG_DIRS, kind, ucfg / kind)


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app, base_url="http://localhost")


# ---------- S1: CORS / Host / mã quản trị ----------
def test_S1_trang_web_la_khong_doc_duoc_api(client):
    r = client.get("/api/v1/audit", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    ok = client.get("/api/v1/audit", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_S1_admin_luon_doi_ma(client):
    assert client.get("/api/v1/admin/files").status_code == 401


def test_S1_chan_dns_rebinding():
    from app.main import app
    evil = TestClient(app, base_url="http://attacker.example")
    assert evil.get("/health").status_code == 400


# ---------- S2: cấu hình người dùng không thay được tầng mặc định ----------
def test_S2_khong_luu_duoc_ma_trung_mac_dinh(client):
    body = {"kind": "jobs", "id": "dong_bac_a",
            "content": json.dumps({"display_name": "x", "fields_catalog": {"a": {"label": "A"}}})}
    assert client.put("/api/v1/config/item", json=body).status_code == 400


def test_S2_tep_trung_ma_dat_tay_van_bi_bo_qua():
    before = config_store.resolve_job_prompt("nhat_ban", "nhat_ban", "tts", "nhat_ban")
    config_store.write_user_config("jobs", "nhat_ban", json.dumps(
        {"display_name": "x", "fields_catalog": {"a": {"label": "A"}},
         "field_check_mode": {"always_check": []}}))
    config_store.set_config_applied("jobs", "nhat_ban", True)
    after = config_store.resolve_job_prompt("nhat_ban", "nhat_ban", "tts", "nhat_ban")
    assert after["fields_catalog"].keys() == before["fields_catalog"].keys()
    assert after["field_check_mode"] == before["field_check_mode"]


# ---------- S5 / S7: trần thân request, kiểm kiểu sửa tay ----------
def test_S5_than_request_qua_lon_bi_413(client):
    big = "x" * (5 * 1024 * 1024)
    r = client.put("/api/v1/config/item", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_S7_ngay_khong_ton_tai_bi_tu_choi():
    from app.domain.documents.rules import _try_parse_date_any, normalize_signed_date
    assert normalize_signed_date("31/02/2025") == ""
    assert _try_parse_date_any("ngày 31/02/2025 và 01/03/2025") == "2025-03-01"


# ---------- E1: mọi thị trường trích xuất được bằng bộ trường 3 tầng ----------
@pytest.mark.parametrize("market,country,job_type", [
    ("tay_a_trung_a_chau_phi", "qatar", "xay_dung"),
    ("chau_au_chau_dai_duong", "duc", "nong_nghiep"),
    ("nhat_ban", "nhat_ban", "cong_viec_tren_bien"),
])
def test_E1_trich_xuat_khong_sap(market, country, job_type):
    from app.domain.documents.intake import resolve_selection
    from app.domain.documents.rules import extract_contract_json
    sel = resolve_selection("", market, country, job_type)
    cj, _ = extract_contract_json("s", "f.pdf", "HỢP ĐỒNG", "HỢP ĐỒNG", sel.job_id,
                                  job_prompt=sel.job_prompt)
    assert set(cj["extracted_fields"]) == set(sel.job_prompt["fields_catalog"])


# ---------- E4: neo kết thúc không cắt giữa văn bản ----------
def test_E4_neo_ket_thuc_chi_tinh_dau_dong():
    from app.domain.documents.ocr import apply_end_anchor
    lines = ["HỢP ĐỒNG CUNG ỨNG LAO ĐỘNG",
             "Điều 4. Các trường hợp chấm dứt hiệu lực của hợp đồng",
             "Điều 5. Tiền dịch vụ người lao động nộp: 90.000.000 VND",
             "Nơi nhận:", "- Như trên"]
    kept, hit = apply_end_anchor(lines)
    assert hit and kept == lines[:3]


# ---------- E5: chống bịa với tiền và đuôi chuỗi ----------
def _cj(key: str, text: str) -> dict:
    return {"extracted_fields": {key: {"label": key, "value": None, "confidence": 0.0, "evidence": {}}},
            "missing_fields": [key], "raw": {"normalized_text": text}}


def test_E5_so_tien_khong_co_trong_bang_chung_bi_loai():
    from app.domain.documents.enrich import merge_llm_extraction
    text = "Tiền dịch vụ người lao động nộp: theo thỏa thuận giữa hai bên ký kết"
    llm = {"tien_dich_vu_nld_nop": {"value": {"amount": 1, "currency": "VND"},
                                    "evidence_quote": "Tiền dịch vụ người lao động nộp"}}
    out = merge_llm_extraction(_cj("tien_dich_vu_nld_nop", text), llm)
    assert out["extracted_fields"]["tien_dich_vu_nld_nop"]["value"] is None

    text2 = "Tiền dịch vụ người lao động nộp: 25.000.000 VND"
    llm2 = {"tien_dich_vu_nld_nop": {"value": {"amount": 25000000, "currency": "VND"},
                                     "evidence_quote": "Tiền dịch vụ người lao động nộp: 25.000.000 VND"}}
    ok = merge_llm_extraction(_cj("tien_dich_vu_nld_nop", text2), llm2)
    assert ok["extracted_fields"]["tien_dich_vu_nld_nop"]["value"]["amount"] == 25000000


def test_E5_duoi_chuoi_bia_them_bi_loai():
    from app.domain.documents.enrich import merge_llm_extraction
    text = "Địa điểm làm việc: tỉnh Aichi và tỉnh Gifu, Nhật Bản.\nThời giờ làm việc: 8 giờ/ngày"
    fake = "tỉnh Aichi và tỉnh Gifu, Nhật Bản và bất kỳ nơi nào khác do chủ sử dụng chỉ định"
    out = merge_llm_extraction(_cj("dia_diem_lam_viec", text), {"dia_diem_lam_viec": {"value": fake}})
    assert out["extracted_fields"]["dia_diem_lam_viec"]["value"] is None
    real = "tỉnh Aichi và tỉnh Gifu, Nhật Bản"
    ok = merge_llm_extraction(_cj("dia_diem_lam_viec", text), {"dia_diem_lam_viec": {"value": real}})
    assert ok["extracted_fields"]["dia_diem_lam_viec"]["value"] == real


# ---------- E6: ngân hàng cụm đáp án không đảo nghĩa ----------
def test_E6_khong_dao_phu_dinh():
    from app.domain.documents.spelling import apply_phrase_bank
    cj = {"extracted_fields": {"cac_khoan_khau_tru": {"label": "x", "value": "Có khoản khấu trừ từ lương",
                                                       "confidence": 0.55, "evidence": {}}},
          "missing_fields": [], "raw": {"normalized_text": ""}}
    out = apply_phrase_bank(cj, "nhat_ban")
    assert out["extracted_fields"]["cac_khoan_khau_tru"]["value"] == "Có khoản khấu trừ từ lương"


# ---------- C2: khoản chi phí lệch giữa hai tài liệu bị chặn ----------
def test_C2_chi_phi_lech_giua_hai_tai_lieu_bi_chan():
    from app.domain.compliance.quality import blocking_fields
    from app.domain.compliance.report import merge_for_check
    def doc(name: str, amount: int) -> dict:
        return {"source_file": name, "contract": {
            "contract_meta": {"source_file": name},
            "extracted_fields": {"chi_phi_khac_nld_nop": {
                "label": "Chi phí khác", "group": "payer",
                "value": {"amount": amount, "currency": "VND"}}}}}
    merged = merge_for_check([doc("1. Văn bản đăng ký hợp đồng.pdf", 0),
                              doc("2. Hợp đồng cung ứng lao động.pdf", 50_000_000)])
    assert "chi_phi_khac_nld_nop" in blocking_fields(merged["contract"]["input_flags"])


# ---------- C3: ngày đầu tiên trong văn bản không phải ngày ký ----------
def test_C3_ngay_suy_dien_do_tin_thap_khong_dung_loc_luat():
    from app.domain.compliance.quality import signed_date_of
    from app.domain.documents.rules import extract_contract_json
    txt = "Căn cứ Giấy phép số 123 cấp ngày 15/06/2019\nHỢP ĐỒNG CUNG ỨNG LAO ĐỘNG"
    cj, _ = extract_contract_json("s", "f.pdf", txt, txt, "nhat_ban")
    assert cj["derived"]["signed_date"]["value"] == "2019-06-15"   # vẫn lưu để tham khảo
    assert signed_date_of(cj) == ""                                # nhưng không làm mốc lọc


# ---------- C4: chữ ký cache đổi theo ngữ cảnh ----------
def test_C4_chu_ky_doi_theo_noi_dung_ho_so():
    from app.domain.compliance.report import request_signature
    assert request_signature(["a"], "2025-01-01", "h1") != request_signature(["a"], "2025-01-01", "h2")


# ---------- C1: CHƯA SỬA (nằm ngoài phạm vi đợt này) ----------
@pytest.mark.xfail(reason="C1 — chưa có rule mức trần tiền dịch vụ (Điều 23 khoản 4 Luật 69/2020)",
                   strict=True)
def test_C1_tien_dich_vu_vuot_tran_phai_khong_dat():
    from app.domain.compliance.factual import check_payer_cost
    verdict, _ = check_payer_cost("tien_dich_vu_nld_nop", {"amount": 900_000_000, "currency": "VND"})
    assert verdict != "PASS"


# ---------- Rà lại sau khi đổi sang Vintern: tệp PDF "bom" ----------
def test_trang_khai_kich_thuoc_khong_lo_bi_ha_ti_le():
    from fpdf import FPDF

    from app.domain.documents.ocr import layout
    pdf = FPDF(format=(5000, 5000))           # trang 5 x 5 mét
    pdf.add_page()
    doc = layout.open_pdf(bytes(pdf.output()))
    try:
        w, h = layout.render_page(doc, 0, 200).size
    finally:
        doc.close()
    assert w * h <= 41_000_000


def test_tep_qua_nhieu_trang_bi_tu_choi(monkeypatch):
    from fpdf import FPDF

    from app.domain.documents.ocr import layout
    monkeypatch.setattr(core.settings, "ocr_max_pages", 3)
    pdf = FPDF()
    for _ in range(5):
        pdf.add_page()
    with pytest.raises(layout.DocumentTooLargeError):
        layout.open_pdf(bytes(pdf.output()))


# ---------- Tối ưu hóa: hành vi không đổi ----------
def test_fold_aligned_giu_dung_do_dai():
    """Bản bỏ dấu GIỮ ĐỘ DÀI là nền của mọi phép cắt theo chỉ số — lệch 1 ký tự là
    giá trị trả về bị xê dịch. Bảng dịch phải cho đúng 1 ký tự đích/ký tự nguồn."""
    import unicodedata

    from app.domain.documents.rules.text import _fold_aligned
    for raw in ("Địa điểm làm việc: tỉnh Aichi", "Tiền lương 184.461 JPY/tháng", "ĐƯỜNG Đ đ"):
        for form in ("NFC", "NFD"):
            s = unicodedata.normalize(form, raw)
            assert len(_fold_aligned(s)) == len(s), (form, raw)
    assert _fold_aligned("Người lao động").lower() == "nguoi lao dong"
    dai = "Điều khoản " * 200          # > 512 ký tự: đi qua nhánh nhớ đệm
    assert len(_fold_aligned(dai)) == len(dai)
    assert _fold_aligned(dai) == _fold_aligned(dai)


def test_match_score_va_tran_tren_khop_voi_difflib():
    """`_match_score` phải bằng đúng max(ratio, coverage×0,95) tính rời, và
    `_score_upper_bound` phải là TRẦN THẬT — nếu không, bộ lọc nhanh sẽ bỏ sót cặp đúng."""
    import difflib
    import random

    from app.domain.documents.spelling import _fold, _match_score, _score_upper_bound

    def diem_roi(a: str, b: str) -> float:
        r = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        m = difflib.SequenceMatcher(None, a, b, autojunk=False)
        cov = sum(x.size for x in m.get_matching_blocks()) / len(a)
        return max(r, cov * 0.95)

    random.seed(7)
    mau = ["Người sử dụng lao động bố trí miễn phí chỗ ở", "Tiền lương cơ bản 150.000 JPY",
           "Không thu tiền ký quỹ", "Theo quy định của pháp luật nước tiếp nhận lao động"]
    for a in mau:
        for b in mau + ["".join(random.sample(a, len(a))), a[: len(a) // 2], a + " và các khoản khác"]:
            fa, fb = _fold(a), _fold(b)
            score = _match_score(fa, fb)
            assert abs(score - diem_roi(fa, fb)) < 1e-9
            assert _score_upper_bound(len(fa), len(fb)) >= score - 1e-9


def test_nho_dem_cau_hinh_van_thay_file_sua(tmp_path, monkeypatch):
    """Cấu hình dịch vụ nhớ đệm theo mtime: sửa tệp phải có hiệu lực ngay, không cần restart."""
    import json
    import time

    from app.store import config as cfg
    d = tmp_path / "services"
    d.mkdir()
    (d / "extraction.json").write_text(json.dumps({"date_rules": {"a": {}}}), encoding="utf-8")
    monkeypatch.setattr(cfg, "SERVICES_DIR", d)
    assert set(cfg.load_extraction_config()["date_rules"]) == {"a"}
    time.sleep(0.01)
    (d / "extraction.json").write_text(json.dumps({"date_rules": {"b": {}}}), encoding="utf-8")
    assert set(cfg.load_extraction_config()["date_rules"]) == {"b"}


# ---------------------------------------------------------------------------
# Rà lỗ hổng lần 2 (21/09/2026)
# ---------------------------------------------------------------------------
def test_vintern_chi_nap_safetensors():
    """Nạp model PHẢI ép `use_safetensors=True` — chặn đường `torch.load` (CVE-2025-32434)."""
    from pathlib import Path

    from app.domain.documents.ocr import vintern

    # Đọc từ FILE chứ không `inspect.getsource`: conftest thay `_load_model` bằng bẫy
    # chặn nạp model thật, nên getsource trả về hàm bẫy.
    src = Path(vintern.__file__).read_text(encoding="utf-8")
    assert "use_safetensors=True" in src


def test_key_lock_khong_phinh_vo_han():
    """`key_lock` phải dọn khóa RẢNH khi chạm trần, nhưng không đụng khóa đang giữ."""
    from app.store import paths

    paths._LOCKS.clear()
    giu = paths.key_lock("dang-giu")
    giu.acquire()
    try:
        for i in range(paths._LOCKS_MAX + 50):
            paths.key_lock(f"phien-{i}")
        assert len(paths._LOCKS) <= paths._LOCKS_MAX + 1
        assert paths.key_lock("dang-giu") is giu      # khóa đang giữ KHÔNG bị thay
    finally:
        giu.release()
        paths._LOCKS.clear()


def test_tran_so_cau_hinh_nguoi_dung(client, monkeypatch):
    """PUT /config/item không cần mã quản trị -> phải có trần số cấu hình MỚI."""
    from app.routers import config as config_router

    monkeypatch.setattr(config_router, "_MAX_USER_CONFIGS", 2)
    c = client
    than = {"kind": "markets", "content": json.dumps(
        {"id": "x", "name": "X", "job_id": "lao_dong_nuoc_ngoai",
         "region_id": "chau_a", "countries": [{"id": "c1", "name": "C1"}],
         "job_types": [{"id": "t1", "name": "T1"}]},
        ensure_ascii=False)}
    for i in range(2):
        r = c.put("/api/v1/config/item", json=than | {"id": f"thi-truong-{i}"})
        assert r.status_code == 200, r.text
    r = c.put("/api/v1/config/item", json=than | {"id": "thi-truong-3"})
    assert r.status_code == 400
    assert "trần" in r.json()["detail"].lower() or "tran" in r.json()["detail"].lower()
    # Ghi ĐÈ mã đã có vẫn phải được (không làm tăng số file).
    r = c.put("/api/v1/config/item", json=than | {"id": "thi-truong-0"})
    assert r.status_code == 200, r.text


def test_bang_bo_dau_co_tran():
    """Bảng dịch tự điền theo mã Unicode phải có trần, không phình theo văn bản lạ."""
    from app.domain.documents.rules.text import _ALIGNED_FOLD, _fold_aligned

    _ALIGNED_FOLD.clear()
    tran = type(_ALIGNED_FOLD)._MAX_ENTRIES
    for start in (0x4E00, 0xAC00):                      # hai khối ký tự lớn
        _fold_aligned("".join(chr(start + i) for i in range(15_000)))
    assert len(_ALIGNED_FOLD) <= tran
    assert _fold_aligned("Tiền lương cơ bản") == "Tien luong co ban"
