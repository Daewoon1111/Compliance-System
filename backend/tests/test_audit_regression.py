"""Hồi quy các lỗ hổng tìm thấy trong đợt audit 17/09/2026 (xem audit/BAO_CAO_AUDIT_*.md).

Mỗi test khóa lại hành vi ĐÃ SỬA; test đỏ nghĩa là lỗ hổng quay lại.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import core
from app.store import config as config_store

FIELD_SET = "hop_dong_mau"
FS_CFG = {"display_name": "Bộ trường test", "fields_catalog": {"a": {"label": "A"}}}


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(core.settings, "admin_token", "ma-quan-tri-test-0123456789")
    monkeypatch.setattr(config_store, "USER_FIELD_SETS_DIR", tmp_path / "user_field_sets")


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app, base_url="http://localhost")


def _field_set() -> dict:
    return config_store.load_field_set(FIELD_SET)


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


# ---------- S2: bộ trường người dùng không thay được tầng mặc định ----------
def test_S2_khong_luu_duoc_ma_trung_mac_dinh(client):
    body = {"id": FIELD_SET, "content": json.dumps(FS_CFG)}
    assert client.put("/api/v1/config/field-set", json=body).status_code == 400


def test_S2_tep_trung_ma_dat_tay_van_bi_bo_qua():
    """Tệp người dùng trùng mã mặc định (đặt tay vào thư mục) không được che bộ mặc định."""
    before = _field_set()
    config_store.write_user_field_set(FIELD_SET, json.dumps(FS_CFG))
    after = _field_set()
    assert after["fields_catalog"].keys() == before["fields_catalog"].keys()
    assert after["field_check_mode"] == before["field_check_mode"]
    same = [i for i in config_store.list_field_sets() if i["id"] == FIELD_SET]
    assert [i["source"] for i in same] == ["default"]


@pytest.mark.parametrize("bad", ["../../services/checks", "..", "a/b", "", "x" * 65])
def test_S2_ma_bo_truong_khong_mo_duoc_tep_ngoai_thu_muc(bad):
    """Mã bộ trường đi thẳng từ form vào tên tệp -> chỉ nhận chữ/số/gạch."""
    with pytest.raises(FileNotFoundError):
        config_store.load_field_set(bad)


# ---------- S5 / S7: trần thân request, kiểm kiểu sửa tay ----------
def test_S5_than_request_qua_lon_bi_413(client):
    big = "x" * (5 * 1024 * 1024)
    r = client.put("/api/v1/config/field-set", content=big,
                   headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_S7_ngay_khong_ton_tai_bi_tu_choi():
    from app.domain.documents.rules import _try_parse_date_any, normalize_signed_date
    assert normalize_signed_date("31/02/2025") == ""
    assert _try_parse_date_any("ngày 31/02/2025 và 01/03/2025") == "2025-03-01"


# ---------- E1: trích xuất theo bộ trường không sập, khung đủ trường ----------
@pytest.mark.parametrize("text", ["", "HỢP ĐỒNG", "Ngày ký: 01/03/2025\nSố lượng: 3"])
def test_E1_trich_xuat_khong_sap(text):
    from app.domain.documents.intake import resolve_selection
    from app.domain.documents.rules import extract_contract_json
    sel = resolve_selection(FIELD_SET)
    cj, _ = extract_contract_json("s", "f.pdf", text, text, sel.job_prompt)
    assert set(cj["extracted_fields"]) == set(sel.job_prompt["fields_catalog"])
    meta = cj["contract_meta"]
    assert (meta["field_set_id"], meta["signed_date_field"]) == (FIELD_SET, "ngay_ky")


# ---------- E4: neo kết thúc không cắt giữa văn bản ----------
def test_E4_neo_ket_thuc_chi_tinh_dau_dong(monkeypatch):
    from app.domain.documents.ocr import apply_end_anchor
    monkeypatch.setattr(core.settings, "ocr_end_anchor", "Nơi nhận|Đại diện bên A")
    lines = ["HỢP ĐỒNG DỊCH VỤ",
             "Điều 4. Hợp đồng hết hiệu lực khi có thông báo gửi tới nơi nhận",
             "Điều 5. Bên B thanh toán theo yêu cầu của đại diện bên A: 90.000.000 VND",
             "Nơi nhận:", "- Như trên"]
    kept, hit = apply_end_anchor(lines)
    assert hit and kept == lines[:3]


# ---------- E5: chống bịa với tiền và đuôi chuỗi ----------
def _cj(key: str, text: str) -> dict:
    return {"extracted_fields": {key: {"label": key, "value": None, "confidence": 0.0, "evidence": {}}},
            "missing_fields": [key], "raw": {"normalized_text": text}}


def test_E5_so_tien_khong_co_trong_bang_chung_bi_loai():
    from app.domain.documents.enrich import merge_llm_extraction
    fc = _field_set()["fields_catalog"]
    key = "gia_tri_hop_dong"
    text = "Giá trị hợp đồng: theo thỏa thuận giữa hai bên ký kết"
    llm = {key: {"value": {"amount": 1, "currency": "VND"}, "evidence_quote": "Giá trị hợp đồng"}}
    out = merge_llm_extraction(_cj(key, text), llm, fc)
    assert out["extracted_fields"][key]["value"] is None

    text2 = "Giá trị hợp đồng: 25.000.000 VND"
    llm2 = {key: {"value": {"amount": 25000000, "currency": "VND"}, "evidence_quote": text2}}
    ok = merge_llm_extraction(_cj(key, text2), llm2, fc)
    assert ok["extracted_fields"][key]["value"]["amount"] == 25000000


def test_E5_duoi_chuoi_bia_them_bi_loai():
    from app.domain.documents.enrich import merge_llm_extraction
    fc = _field_set()["fields_catalog"]
    key = "giai_quyet_tranh_chap"
    text = "Giải quyết tranh chấp: Tòa án nhân dân thành phố Hà Nội.\nĐiều 9. Hiệu lực hợp đồng"
    fake = "Tòa án nhân dân thành phố Hà Nội hoặc trọng tài thương mại do bên A chỉ định"
    out = merge_llm_extraction(_cj(key, text), {key: {"value": fake}}, fc)
    assert out["extracted_fields"][key]["value"] is None
    real = "Tòa án nhân dân thành phố Hà Nội"
    ok = merge_llm_extraction(_cj(key, text), {key: {"value": real}}, fc)
    assert ok["extracted_fields"][key]["value"] == real


# ---------- C2: gộp tài liệu — tài liệu đầu ưu tiên, tài liệu sau chỉ bù chỗ trống ----------
def test_C2_gop_tai_lieu_khong_ghi_de_tai_lieu_chinh():
    from app.domain.compliance.report import merge_for_check

    def doc(name: str, amount: int | None, ben_b: str | None) -> dict:
        return {"source_file": name, "contract": {
            "contract_meta": {"source_file": name, "field_set_id": FIELD_SET},
            "extracted_fields": {
                "gia_tri_hop_dong": {"label": "Giá trị hợp đồng",
                                     "value": None if amount is None
                                     else {"amount": amount, "currency": "VND"}},
                "ben_b": {"label": "Bên B", "value": ben_b}}}}
    merged = merge_for_check([doc("1. Hợp đồng.pdf", 100_000_000, None),
                              doc("2. Phụ lục.pdf", 50_000_000, "Công ty Beta")])
    ef = merged["contract"]["extracted_fields"]
    assert ef["gia_tri_hop_dong"]["value"]["amount"] == 100_000_000
    assert ef["ben_b"]["value"] == "Công ty Beta"
    assert merged["contract"]["missing_fields"] == []


# ---------- C3: ngày đầu tiên trong văn bản không phải ngày ký ----------
def test_C3_ngay_suy_dien_do_tin_thap_khong_dung_loc_luat():
    from app.domain.compliance.quality import signed_date_of
    from app.domain.documents.rules import extract_contract_json
    txt = "Căn cứ Giấy phép số 123 cấp ngày 15/06/2019\nHỢP ĐỒNG DỊCH VỤ"
    cj, _ = extract_contract_json("s", "f.pdf", txt, txt, _field_set())
    assert cj["derived"]["signed_date"]["value"] == "2019-06-15"   # vẫn lưu để tham khảo
    assert signed_date_of(cj) == ""                                # nhưng không làm mốc lọc


def test_C3_bo_truong_khong_khai_ngay_ky_thi_khong_suy_dien():
    from app.domain.compliance.quality import signed_date_of
    from app.domain.documents.rules import extract_contract_json
    fs = _field_set()
    fs.pop("signed_date_field")
    txt = "Ký ngày 01/03/2025\nHỢP ĐỒNG DỊCH VỤ"
    cj, _ = extract_contract_json("s", "f.pdf", txt, txt, fs)
    assert cj["derived"]["signed_date"]["value"] is None
    assert signed_date_of(cj) == ""


# ---------- C4: chữ ký cache đổi theo ngữ cảnh ----------
def test_C4_chu_ky_doi_theo_noi_dung_ho_so():
    from app.domain.compliance.report import request_signature
    assert request_signature(["a"], "2025-01-01", "h1") != request_signature(["a"], "2025-01-01", "h2")


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
    for raw in ("Giải quyết tranh chấp: Tòa án", "Giá trị hợp đồng 120.000.000 VND", "ĐƯỜNG Đ đ"):
        for form in ("NFC", "NFD"):
            s = unicodedata.normalize(form, raw)
            assert len(_fold_aligned(s)) == len(s), (form, raw)
    assert _fold_aligned("Phương thức thanh toán").lower() == "phuong thuc thanh toan"
    dai = "Điều khoản " * 200          # > 512 ký tự: đi qua nhánh nhớ đệm
    assert len(_fold_aligned(dai)) == len(dai)
    assert _fold_aligned(dai) == _fold_aligned(dai)


def test_nho_dem_cau_hinh_van_thay_file_sua(tmp_path, monkeypatch):
    """Cấu hình dịch vụ nhớ đệm theo mtime: sửa tệp phải có hiệu lực ngay, không cần restart."""
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


def test_nho_dem_bo_truong_van_thay_file_sua_va_tra_ban_sao(client):
    """Bộ trường cũng nhớ đệm theo mtime; nơi gọi lỡ sửa bản trả về thì không được làm
    hỏng bộ trường của mọi phiên sau."""
    import time

    client.put("/api/v1/config/field-set", json={"id": "bo_test", "content": json.dumps(FS_CFG)})
    fs = config_store.load_field_set("bo_test")
    fs["fields_catalog"].clear()
    assert config_store.load_field_set("bo_test")["fields_catalog"], "bản đệm bị sửa lây"
    time.sleep(0.01)
    client.put("/api/v1/config/field-set",
               json={"id": "bo_test", "content": json.dumps(dict(FS_CFG, display_name="Mới"))})
    assert config_store.load_field_set("bo_test")["display_name"] == "Mới"


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


def test_tran_so_bo_truong_nguoi_dung(client, monkeypatch):
    """PUT /config/field-set không cần mã quản trị -> phải có trần số bộ trường MỚI."""
    from app.routers import config as config_router

    monkeypatch.setattr(config_router, "_MAX_USER_FIELD_SETS", 2)
    than = {"content": json.dumps(FS_CFG, ensure_ascii=False)}
    for i in range(2):
        r = client.put("/api/v1/config/field-set", json=than | {"id": f"bo-{i}"})
        assert r.status_code == 200, r.text
    r = client.put("/api/v1/config/field-set", json=than | {"id": "bo-3"})
    assert r.status_code == 400
    assert "trần" in r.json()["detail"].lower()
    assert config_store.user_field_set_count() == 2
    # Ghi ĐÈ mã đã có vẫn phải được (không làm tăng số file).
    r = client.put("/api/v1/config/field-set", json=than | {"id": "bo-0"})
    assert r.status_code == 200, r.text


def test_bang_bo_dau_co_tran():
    """Bảng dịch tự điền theo mã Unicode phải có trần, không phình theo văn bản lạ."""
    from app.domain.documents.rules.text import _ALIGNED_FOLD, _fold_aligned

    _ALIGNED_FOLD.clear()
    tran = type(_ALIGNED_FOLD)._MAX_ENTRIES
    for start in (0x4E00, 0xAC00):                      # hai khối ký tự lớn
        _fold_aligned("".join(chr(start + i) for i in range(15_000)))
    assert len(_ALIGNED_FOLD) <= tran
    assert _fold_aligned("Giá trị hợp đồng") == "Gia tri hop dong"
