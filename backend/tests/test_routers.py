"""Test TẦNG API (routers) — xác thực quản trị, chặn path traversal, hạn mức tải lên,
kẹp tham số truy vấn, vòng đời bộ trường của người dùng, thống kê và chỉ số kỹ thuật.

Nguyên tắc của file này:
  · KHÔNG chạm dữ liệu thật — temp/, audit.jsonl và data/user_config/field_sets/ đều bị
    trỏ sang thư mục tạm của pytest (fixture `isolate_store`, autouse).
  · KHÔNG gọi LLM/OCR/ChromaDB — OCR và chuỗi kiểm tra được thay bằng bản giả.
  · `TestClient(app, base_url="http://localhost")` dùng TRẦN (không `with`): vào `with` là chạy lifespan, mà
    lifespan nạp model OCR ở thread nền — đúng thứ conftest dựng rào để chặn.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import core
from app import progress as pg
from app.domain.compliance.quality import signed_date_of
from app.domain.documents import intake
from app.domain.documents.rules import extract_contract_json
from app.main import app
from app.routers import admin as admin_router
from app.routers import meta
from app.routers import sessions as sessions_router
from app.store import audit as audit_store
from app.store import config as config_store
from app.store import get_paths, read_json

ADMIN_TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
# Bộ trường mặc định có thật trong prompts/field_sets/ — dùng cho các ca cần qua `resolve_selection`.
FIELD_SET = "hop_dong_mau"
FORM = {"field_set": FIELD_SET}


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Mọi đường ghi của tầng API trỏ vào tmp_path. Thiếu fixture này thì chạy test
    một lần là xóa sạch nhật ký kiểm tra và bộ trường thật của người dùng."""
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(core.settings, "admin_token", ADMIN_TOKEN)
    monkeypatch.setattr(audit_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config_store, "USER_FIELD_SETS_DIR", tmp_path / "user_field_sets")
    monkeypatch.setattr(config_store, "ACTIVE_FILE", tmp_path / "active.json")


@pytest.fixture()
def client():
    return TestClient(app, base_url="http://localhost")


def _seed_session(tmp_path, documents=None, report=None) -> str:
    """Dựng sẵn một phiên trên đĩa (không qua OCR) để test các endpoint đọc/sửa."""
    sid = str(uuid.uuid4())
    base = tmp_path / "temp" / sid
    base.mkdir(parents=True)
    if documents is not None:
        (base / "documents.json").write_text(
            json.dumps(documents, ensure_ascii=False), encoding="utf-8")
    if report is not None:
        (base / "final_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return sid


# ===========================================================================
# 1) CHẶN PATH TRAVERSAL qua session_id
# ===========================================================================
# `get_paths` ghép session_id thẳng vào os.path.join. Router của Starlette chặn '/',
# nhưng trên Windows os.path.join coi CẢ '\' là dấu phân cách -> '..\..\x' thoát khỏi
# temp/ và đọc được documents.json / final_report.json ở thư mục bất kỳ.
EVIL_IDS = [
    "..%5C..%5C..%5Cwindows",   # '\' — đường thoát THẬT trên Windows
    "..",
    "not-a-uuid",
    "0000",
    "%2e%2e%5c%2e%2e",
]


@pytest.mark.parametrize("bad", EVIL_IDS)
@pytest.mark.parametrize("path", [
    "/api/v1/sessions/{}/documents",
    "/api/v1/sessions/{}/report",
    "/api/v1/sessions/{}/export.pdf",
])
def test_session_id_khong_phai_uuid_bi_chan(client, bad, path):
    assert client.get(path.format(bad)).status_code == 404


# Các đoạn thoát thư mục mà os.path.join(temp_dir, ...) sẽ giải ra ĐÚNG thư mục CHA
# của temp/ — nơi ta trồng sẵn file mồi ở test dưới.
ESCAPE_TO_PARENT = ["..", "%2e%2e", "..%5C.", "..%2F."]


@pytest.mark.parametrize("evil", ESCAPE_TO_PARENT)
def test_traversal_khong_doc_duoc_file_ngoai_temp(client, tmp_path, evil):
    """Bài kiểm THẬT của rào chặn: trồng `documents.json` NGAY TRÊN temp/ rồi yêu cầu
    một session_id thoát lên một cấp.

    Bài `..._bi_chan` ở trên KHÔNG đủ: bỏ hẳn rào chặn nó vẫn xanh, vì đường thoát trỏ
    tới chỗ không có file nên rơi vào 404 'không tìm thấy phiên' — xanh vì lý do sai.
    Ở đây file ĐÃ CÓ THẬT: rào hỏng là nội dung rò ra ngay trong body."""
    (tmp_path / "temp").mkdir(parents=True, exist_ok=True)
    (tmp_path / "documents.json").write_text(
        json.dumps({"documents": [{"doc_id": "BI_RO_RI", "source_file": "mat.pdf"}]}),
        encoding="utf-8")

    r = client.get(f"/api/v1/sessions/{evil}/documents")
    assert "BI_RO_RI" not in r.text, "path traversal đọc được file ngoài temp/"
    assert r.status_code == 404


def test_session_id_hop_le_van_chay_binh_thuong(client, tmp_path):
    """Rào chặn phải HẸP: UUID thật vẫn phải đi qua, nếu không là chặn nhầm cả hệ."""
    sid = _seed_session(tmp_path, documents={"documents": []})
    assert client.get(f"/api/v1/sessions/{sid}/documents").status_code == 200


def test_phien_khong_ton_tai_tra_404(client):
    sid = str(uuid.uuid4())
    assert client.get(f"/api/v1/sessions/{sid}/documents").status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}/report").status_code == 404


# ===========================================================================
# 2) XÁC THỰC QUẢN TRỊ
# ===========================================================================
ADMIN_GET_PATHS = ["/api/v1/admin/files", "/api/v1/admin/file?rel=rules/x.md"]


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
def test_admin_thieu_token_tra_401(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer sai-token"},
    {"Authorization": "Basic " + ADMIN_TOKEN},   # sai lược đồ -> không được nhận
    {"X-Admin-Token": "sai-token"},
    {},
])
def test_admin_token_sai_tra_401(client, headers):
    assert client.get("/api/v1/admin/files", headers=headers).status_code == 401


def test_admin_token_dung_thi_vao_duoc(client):
    r = client.get("/api/v1/admin/files", headers=AUTH)
    assert r.status_code == 200
    files = r.json()["files"]
    assert {f["group"] for f in files} <= {"field_sets", "rules"}
    assert f"field_sets/{FIELD_SET}.json" in {f["rel"] for f in files}


def test_admin_chap_nhan_header_cu_x_admin_token(client):
    assert client.get("/api/v1/admin/files",
                      headers={"X-Admin-Token": ADMIN_TOKEN}).status_code == 200


def test_admin_token_co_dau_tra_401_chu_khong_phai_500(client, monkeypatch):
    """`secrets.compare_digest` ném TypeError khi MỘT trong hai vế ngoài ASCII. Đặt
    admin_token có dấu trong .env là mọi lần gõ sai mã thành lỗi 500 (lộ traceback)
    thay vì 401. So sánh trên bytes thì không.

    Chỉ kiểm được chiều 'gõ sai': header HTTP bắt buộc ASCII nên mã có dấu KHÔNG BAO
    GIỜ gửi lên đúng được — tức đặt mã có dấu là tự khóa mình ngoài cửa, và trước bản
    vá thì còn kèm 500."""
    monkeypatch.setattr(core.settings, "admin_token", "mã-quản-trị-có-dấu")
    assert client.get("/api/v1/admin/files",
                      headers={"Authorization": "Bearer sai"}).status_code == 401
    assert client.get("/api/v1/admin/files",
                      headers={"X-Admin-Token": "sai"}).status_code == 401


def test_admin_token_rong_thi_tu_choi(client, monkeypatch):
    """Không còn chế độ tắt xác thực: mã trống (lỗi cấu hình) -> 401, không phải mở toang."""
    monkeypatch.setattr(core.settings, "admin_token", "")
    assert client.get("/api/v1/admin/files").status_code == 401


# ===========================================================================
# 3) ADMIN — chặn path traversal ở tham số `rel`, kiểm nội dung trước khi ghi
# ===========================================================================
@pytest.fixture()
def admin_dirs(tmp_path, monkeypatch):
    """Trỏ hai nhóm của trang Quản trị sang thư mục tạm: test GHI không được chạm
    bộ trường mặc định thật — kể cả khi rào kiểm tra hỏng."""
    dirs = {"field_sets": tmp_path / "adm_field_sets", "rules": tmp_path / "adm_rules"}
    for grp, d in dirs.items():
        d.mkdir()
        monkeypatch.setitem(admin_router._ADMIN_DIRS, grp, d)
    return dirs


@pytest.mark.parametrize("rel", [
    "rules/../../../../etc/passwd",
    "rules/..\\..\\x.md",
    "khong_co_nhom.md",          # thiếu dấu '/' -> không phân giải được nhóm
    "linh_tinh/x.md",            # nhóm không tồn tại
    "rules/khac.json",           # nhóm 'rules' CHỈ nhận một tệp JSON là corpus.json
    "field_sets/x.md",           # bộ trường chỉ là .json
    "rules/x.exe",               # đuôi ngoài .json/.md
])
def test_admin_rel_khong_hop_le_bi_chan(client, rel):
    assert client.get("/api/v1/admin/file", params={"rel": rel},
                      headers=AUTH).status_code == 400


@pytest.mark.parametrize("content,thieu", [
    ("{ khong-phai-json", "JSON"),
    (json.dumps({"display_name": "X"}), "fields_catalog"),
])
def test_admin_ghi_bo_truong_hong_bi_tu_choi_truoc_khi_ghi(client, admin_dirs, content, thieu):
    """JSON sai cú pháp hoặc bộ trường thiếu khóa bắt buộc phải bị chặn TRƯỚC khi chạm
    đĩa — nếu không, một dấu phẩy thừa làm hỏng bộ trường mặc định của cả hệ."""
    r = client.put("/api/v1/admin/file", headers=AUTH,
                   json={"rel": "field_sets/moi.json", "content": content})
    assert r.status_code == 400
    assert thieu in r.json()["detail"]
    assert not (admin_dirs["field_sets"] / "moi.json").exists()


def test_admin_ghi_bo_truong_hop_le_thi_hien_trong_danh_sach(client, admin_dirs):
    content = json.dumps({"display_name": "Biên bản nghiệm thu",
                          "fields_catalog": {"so_bien_ban": {"label": "Số biên bản"}}},
                         ensure_ascii=False)
    r = client.put("/api/v1/admin/file", headers=AUTH,
                   json={"rel": "field_sets/bien_ban.json", "content": content})
    assert r.status_code == 200 and r.json()["reseeded"] is False
    files = client.get("/api/v1/admin/files", headers=AUTH).json()["files"]
    assert {"rel": "field_sets/bien_ban.json", "display": "Biên bản nghiệm thu"}.items() \
        <= next(f for f in files if f["name"] == "bien_ban.json").items()


def test_admin_ghi_file_ngoai_thu_muc_bi_chan(client):
    r = client.put("/api/v1/admin/file", headers=AUTH,
                   json={"rel": "rules/../../../evil.md", "content": "x"})
    assert r.status_code == 400


# ===========================================================================
# 4) XÓA NHẬT KÝ — thao tác không hoàn tác, phải có mã quản trị
# ===========================================================================
def test_xoa_nhat_ky_khong_co_token_tra_401(client, tmp_path):
    audit_store.AUDIT_FILE.write_text(
        json.dumps({"session_id": "s1", "documents": []}) + "\n", encoding="utf-8")
    assert client.delete("/api/v1/audit").status_code == 401
    # Quan trọng hơn mã lỗi: file PHẢI còn nguyên.
    assert audit_store.AUDIT_FILE.exists()


def test_xoa_nhat_ky_co_token_thi_xoa_that(client):
    audit_store.AUDIT_FILE.write_text(
        json.dumps({"session_id": "s1", "documents": []}) + "\n", encoding="utf-8")
    r = client.delete("/api/v1/audit", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["stats_reset"] is True
    assert client.get("/api/v1/stats").json()["total_runs"] == 0


# ===========================================================================
# 5) HẠN MỨC TẢI LÊN + BỘ TRƯỜNG KHÔNG HỢP LỆ
# ===========================================================================
def _pdf(name: str, size: int = 8) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, b"%PDF-" + b"0" * size, "application/pdf"))


def test_thieu_hoan_toan_truong_files_tra_422(client):
    """`files: list[UploadFile] = File(...)` là BẮT BUỘC nên Pydantic chặn ở 422
    TRƯỚC khi vào thân hàm — nhánh `if not files -> 400` trong router không bao giờ
    chạy được qua HTTP (multipart không diễn đạt được 'danh sách rỗng')."""
    assert client.post("/api/v1/sessions", data=FORM).status_code == 422


def test_vuot_so_file_toi_da_tra_413(client, monkeypatch):
    monkeypatch.setattr(sessions_router, "MAX_FILES", 2)
    r = client.post("/api/v1/sessions", data=FORM,
                    files=[_pdf(f"f{i}.pdf") for i in range(3)])
    assert r.status_code == 413


def test_file_qua_lon_tra_413_kem_ten_file(client, monkeypatch):
    monkeypatch.setattr(sessions_router, "MAX_FILE_BYTES", 10)
    r = client.post("/api/v1/sessions", data=FORM, files=[_pdf("qua_lon.pdf", size=200)])
    assert r.status_code == 413
    assert "qua_lon.pdf" in r.json()["detail"]


def test_tong_dung_luong_vuot_tra_413(client, monkeypatch):
    monkeypatch.setattr(sessions_router, "MAX_FILE_BYTES", 10_000)
    monkeypatch.setattr(sessions_router, "MAX_TOTAL_BYTES", 100)
    r = client.post("/api/v1/sessions", data=FORM,
                    files=[_pdf(f"f{i}.pdf", size=60) for i in range(3)])
    assert r.status_code == 413


class _FileGia:
    """UploadFile giả GHI LẠI cỡ đã yêu cầu đọc — bài kiểm chính là con số đó."""

    def __init__(self, size: int | None, n_bytes: int = 5_000):
        self.filename, self.size = "qua_lon.pdf", size
        self._data = b"%PDF-" + b"0" * n_bytes
        self.da_doc: list[int] = []

    async def read(self, n: int = -1) -> bytes:
        self.da_doc.append(n)
        return self._data if n < 0 else self._data[:n]


@pytest.mark.parametrize("biet_co", [True, False])
def test_tran_chan_TRUOC_khi_nap_vao_ram(biet_co):
    """Trần phải chặn TRƯỚC lúc đọc: đọc trọn rồi mới đo thì RAM đã cạn từ trước khi
    có mã 413 — cái trần không bảo vệ được đúng thứ nó sinh ra để bảo vệ.

    Hai đường: biết trước cỡ tệp (`UploadFile.size`) thì chặn mà KHÔNG đọc byte nào;
    không biết cỡ (phần multipart không khai độ dài) thì vẫn phải đọc CÓ TRẦN."""
    f = _FileGia(size=5_000 if biet_co else None)
    with pytest.raises(HTTPException) as err:
        asyncio.run(sessions_router._read_capped(f, budget=10))
    assert err.value.status_code == 413
    if biet_co:
        assert f.da_doc == [], "biết cỡ rồi thì không được đọc byte nào"
    else:
        assert f.da_doc and all(0 < n <= 11 for n in f.da_doc), \
            "đọc không giới hạn — trần bị đo SAU khi nạp RAM"


def test_tra_ve_du_byte_khi_nam_trong_tran():
    """Trần chặn tệp quá cỡ nhưng KHÔNG được cắt cụt tệp hợp lệ — cắt cụt thì OCR
    đọc thiếu trang mà không có lỗi nào nổi lên."""
    f = _FileGia(size=None, n_bytes=100)
    assert asyncio.run(sessions_router._read_capped(f, budget=10_000)) == f._data


def test_file_khong_phai_pdf_tra_400_kem_ten_file(client):
    """Sai định dạng là lỗi của người gửi (400), không phải sự cố máy chủ (500) — và
    câu trả lời phải gọi đúng tên tệp hỏng trong một lượt nhiều tệp."""
    r = client.post("/api/v1/sessions", data=FORM,
                    files=[_pdf("that.pdf"),
                           ("files", ("gia.pdf", b"PK\x03\x04rac", "application/pdf"))])
    assert r.status_code == 400
    assert "gia.pdf" in r.json()["detail"]


@pytest.mark.parametrize("fs", [
    "khong-ton-tai",                 # mã hợp lệ nhưng không có tệp
    "../../services/checks",         # mã đi thẳng vào tên tệp -> phải chặn traversal
])
def test_bo_truong_khong_hop_le_tra_400(client, fs):
    r = client.post("/api/v1/sessions", data={"field_set": fs}, files=[_pdf("a.pdf")])
    assert r.status_code == 400


# ===========================================================================
# 6) LUỒNG TẢI LÊN với OCR giả + đệm phiên
# ===========================================================================
VAN_BAN = ("HỢP ĐỒNG DỊCH VỤ\nSố hợp đồng: 15/2025/HĐDV\nNgày ký: 01/03/2025\n"
           "Bên A: Công ty TNHH Alpha\nBên B: Công ty Cổ phần Beta\n"
           "Giá trị hợp đồng: 120.000.000 VND\nSố lượng: 3")


@pytest.fixture()
def fake_ocr(monkeypatch):
    """Thay OCR bằng trích xuất bằng luật trên một văn bản cố định; ghi lại tên file
    đã 'đọc' để biết lượt tải lên có đi qua đệm hay không."""
    da_doc: list[str] = []

    async def _process(session_id, _data, filename, job_prompt, **_k):
        da_doc.append(filename)
        cj, _ = extract_contract_json(session_id, filename, VAN_BAN, VAN_BAN, job_prompt)
        return {"source_file": filename, "ocr": {}, "contract": cj,
                "missing_fields": cj["missing_fields"]}

    monkeypatch.setattr(intake, "process_file", _process)
    monkeypatch.setattr(sessions_router, "prefetch_field_set_regulations", lambda _jp: None)
    return da_doc


def test_tai_len_trich_xuat_theo_bo_truong_da_chon(client, fake_ocr):
    r = client.post("/api/v1/sessions", data=FORM, files=[_pdf("hd.pdf"), _pdf("pl.pdf", 9)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["doc_id"] for d in body["documents"]] == ["doc1", "doc2"]
    contract = body["documents"][0]["extracted_json"]
    meta_ = contract["contract_meta"]
    assert (meta_["field_set_id"], meta_["signed_date_field"]) == (FIELD_SET, "ngay_ky")
    assert meta_["session_id"] == body["session_id"]
    assert contract["extracted_fields"]["gia_tri_hop_dong"]["value"]["amount"] == 120_000_000
    assert signed_date_of(contract) == "2025-03-01"
    # Phiên đã ghi xuống đĩa và đọc lại được.
    assert client.get(f"/api/v1/sessions/{body['session_id']}/documents").status_code == 200


def test_tai_lai_cung_bo_file_dung_lai_phien_cu(client, fake_ocr):
    """Cùng tập file (khác thứ tự) + cùng bộ trường -> trả phiên cũ, KHÔNG OCR lại."""
    a, b = _pdf("hd.pdf"), _pdf("pl.pdf", 9)
    sid = client.post("/api/v1/sessions", data=FORM, files=[a, b]).json()["session_id"]
    n = len(fake_ocr)
    r = client.post("/api/v1/sessions", data=FORM, files=[b, a])
    assert r.json()["session_id"] == sid
    assert len(fake_ocr) == n, "lượt tải lại vẫn chạy OCR"


def test_doi_bo_truong_thi_khong_dung_lai_phien(client, fake_ocr):
    client.put("/api/v1/config/field-set", json={"id": "bo_khac", "content": json.dumps(FS_CFG)})
    sid1 = client.post("/api/v1/sessions", data=FORM, files=[_pdf("hd.pdf")]).json()["session_id"]
    r = client.post("/api/v1/sessions", data={"field_set": "bo_khac"}, files=[_pdf("hd.pdf")])
    assert r.status_code == 200
    assert r.json()["session_id"] != sid1


# ===========================================================================
# 7) SỬA TAY GIÁ TRỊ TRÍCH XUẤT (PATCH .../fields)
# ===========================================================================
def _doc_store() -> dict:
    return {"documents": [{
        "doc_id": "doc1",
        "source_file": "a.pdf",
        "contract": {
            "extracted_fields": {
                "gia_tri_hop_dong": {"label": "Giá trị hợp đồng", "value": None,
                                     "confidence": 0.0, "value_type": "money"},
                "so_luong": {"label": "Số lượng", "value": None, "confidence": 0.0,
                             "value_type": "number"},
            },
            "missing_fields": ["gia_tri_hop_dong", "so_luong"],
            "input_flags": [
                {"code": "LOW_CONFIDENCE", "field": "gia_tri_hop_dong", "block_field": True},
            ],
            "contract_meta": {"field_set_id": FIELD_SET},
        },
        "missing_fields": ["gia_tri_hop_dong", "so_luong"],
    }]}


def _patch(client, sid, fields, doc="doc1"):
    return client.patch(f"/api/v1/sessions/{sid}/documents/{doc}/fields", json={"fields": fields})


def test_sua_tay_dien_gia_tri_va_go_co_chan(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    r = _patch(client, sid, {"gia_tri_hop_dong": "120.000.000 VND"})
    assert r.status_code == 200
    assert r.json()["updated"] == ["gia_tri_hop_dong"]
    assert r.json()["missing_fields"] == ["so_luong"]

    saved = read_json(get_paths(sid).documents_json)["documents"][0]["contract"]
    fld = saved["extracted_fields"]["gia_tri_hop_dong"]
    assert fld["value"] == "120.000.000 VND"
    assert fld["confidence"] == 1.0
    assert fld["evidence"]["source"] == "USER_EDIT"
    # Người dùng đã tự xác nhận -> cờ chất lượng CHẶN của đúng trường đó phải biến mất.
    assert saved["input_flags"] == []


@pytest.mark.parametrize("val,ok,luu", [
    ("5", True, 5),
    ("ba cái", False, None),
    (True, False, None),
    (-1, False, None),
])
def test_sua_tay_truong_so_nan_dung_kieu(client, tmp_path, val, ok, luu):
    """Giá trị sửa tay coi là ĐÚNG (conf 1.0) nên phải đúng KIỂU của trường."""
    sid = _seed_session(tmp_path, documents=_doc_store())
    r = _patch(client, sid, {"so_luong": val})
    assert (r.status_code == 200) is ok
    saved = read_json(get_paths(sid).documents_json)["documents"][0]["contract"]
    assert saved["extracted_fields"]["so_luong"]["value"] == luu


def test_sua_tay_gia_tri_rong_dua_truong_ve_thieu(client, tmp_path):
    store = _doc_store()
    store["documents"][0]["contract"]["extracted_fields"]["so_luong"]["value"] = 3
    store["documents"][0]["contract"]["missing_fields"] = ["gia_tri_hop_dong"]
    sid = _seed_session(tmp_path, documents=store)
    r = _patch(client, sid, {"so_luong": None})
    assert r.json()["missing_fields"] == ["gia_tri_hop_dong", "so_luong"]


def test_sua_tay_giu_gia_tri_may_doc_mot_lan(client, tmp_path):
    """Giá trị MÁY ĐỌC được giữ ở lần sửa đầu (nguồn của CER/độ chính xác trường)."""
    store = _doc_store()
    store["documents"][0]["contract"]["extracted_fields"]["so_luong"]["value"] = 8
    sid = _seed_session(tmp_path, documents=store)
    _patch(client, sid, {"so_luong": "5"})
    _patch(client, sid, {"so_luong": "6"})
    fld = read_json(get_paths(sid).documents_json)["documents"][0]["contract"][
        "extracted_fields"]["so_luong"]
    assert fld["value"] == 6 and fld["machine"] == {"value": 8}


def test_sua_tay_xoa_bao_cao_cu(client, tmp_path):
    """Báo cáo cũ tính trên dữ liệu cũ — không xóa thì 'kiểm tra lại' trả cache sai."""
    sid = _seed_session(tmp_path, documents=_doc_store(), report={"overall_verdict": "PASS"})
    _patch(client, sid, {"gia_tri_hop_dong": "120.000.000 VND"})
    assert not (tmp_path / "temp" / sid / "final_report.json").exists()


def test_sua_tay_truong_ngoai_bo_truong_bi_bo_qua(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    assert _patch(client, sid, {"truong_bia_dat": "x"}).status_code == 400


def test_sua_tay_thieu_fields_tra_400(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    assert _patch(client, sid, {}).status_code == 400


def test_sua_tay_sai_doc_id_tra_404(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    assert _patch(client, sid, {"so_luong": "1"}, doc="doc99").status_code == 404


# ===========================================================================
# 8) THỐNG KÊ / NHẬT KÝ — kẹp tham số truy vấn, tìm kiếm, lọc theo bộ trường
# ===========================================================================
def _seed_audit(n: int) -> None:
    audit_store.AUDIT_FILE.write_text(
        "".join(json.dumps({"session_id": f"s{i}", "documents": []}) + "\n"
                for i in range(n)),
        encoding="utf-8")


def test_limit_am_bi_kep_ve_1_chu_khong_cat_duoi_danh_sach(client):
    """`limit` đi thẳng vào `records[::-1][:limit]`. KHÔNG kẹp thì `limit=-1` thành
    lát cắt ÂM: bỏ bản ghi CUỐI và vẫn trả 200 — sai dữ liệu mà không ai biết."""
    _seed_audit(3)
    assert len(client.get("/api/v1/audit", params={"limit": -1}).json()["records"]) == 1
    assert len(client.get("/api/v1/audit", params={"limit": 0}).json()["records"]) == 1


def test_limit_khong_lo_van_tra_du_ban_ghi_dang_co(client):
    """Kẹp trần không được cắt nhầm khi nhật ký còn ít hơn trần."""
    _seed_audit(3)
    r = client.get("/api/v1/audit", params={"limit": 10**9})
    assert r.status_code == 200
    assert len(r.json()["records"]) == 3


def _rec(sid: str, fs_id: str, fs_name: str, verdict: str, src: str = "hd.pdf") -> dict:
    return {"session_id": sid, "field_set_id": fs_id, "field_set_name": fs_name,
            "source_files": [src],
            "documents": [{"doc_id": "merged", "source_file": src, "overall_verdict": verdict}]}


def _seed_records(*recs: dict) -> None:
    audit_store.AUDIT_FILE.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")


def test_audit_doc_duoc_ban_ghi_va_thong_ke_theo_bo_truong(client):
    _seed_records(_rec("s1", FIELD_SET, "Hợp đồng (mẫu chung)", "PASS"),
                  _rec("s2", FIELD_SET, "Hợp đồng (mẫu chung)", "FAIL"),
                  _rec("s3", "bien_ban", "Biên bản nghiệm thu", "PASS"))
    assert len(client.get("/api/v1/audit").json()["records"]) == 3
    stats = client.get("/api/v1/stats").json()
    assert stats["total_runs"] == 3
    assert stats["totals"]["PASS"] == 2 and stats["totals"]["FAIL"] == 1
    assert stats["by_field_set"]["Hợp đồng (mẫu chung)"]["total"] == 2


def test_audit_tim_kiem_bo_dau(client):
    _seed_records(_rec("s1", FIELD_SET, "Hợp đồng (mẫu chung)", "PASS", "Phụ lục giá.pdf"))
    q = lambda s: client.get("/api/v1/audit", params={"q": s}).json()["records"]  # noqa: E731
    assert len(q("HOP DONG (mau chung")) == 1
    assert len(q("phu luc gia")) == 1, "tìm theo tên file"
    assert len(q("bien ban")) == 0


@pytest.mark.parametrize("params,ids", [
    ({"field_set": FIELD_SET}, ["s2", "s1"]),
    ({"field_set": "Biên bản nghiệm thu"}, ["s3"]),          # lọc theo tên hiển thị cũng được
    ({"verdict": "FAIL"}, ["s2"]),
    ({"field_set": FIELD_SET, "verdict": "PASS"}, ["s1"]),
])
def test_audit_loc_theo_bo_truong_va_ket_qua(client, params, ids):
    _seed_records(_rec("s1", FIELD_SET, "Hợp đồng (mẫu chung)", "PASS"),
                  _rec("s2", FIELD_SET, "Hợp đồng (mẫu chung)", "FAIL"),
                  _rec("s3", "bien_ban", "Biên bản nghiệm thu", "PASS"))
    recs = client.get("/api/v1/audit", params=params).json()["records"]
    assert [r["session_id"] for r in recs] == ids


def _run(sid: str, ts: str, *, files=1, pages=0, mb_bytes=0, ocr=0.0, check=0.0) -> dict:
    return {"session_id": sid, "ts": ts, "num_documents": files, "total_pages": pages,
            "total_bytes": mb_bytes, "ocr_seconds": ocr, "documents": [],
            "metrics": {"seconds": check}}


def test_chi_so_ky_thuat_gom_theo_phien_khong_theo_ngay():
    """Hai lượt kiểm tra CÙNG phiên phải thành MỘT dòng và cộng dồn chi phí.

    Gom theo ngày thì hai lượt này lẫn với hồ sơ khác chạy cùng hôm, và số 'mỗi
    trang' hết so sánh được giữa các hồ sơ."""
    today = datetime.now().date().isoformat()
    _seed_records(
        _run("s1", f"{today}T09:00:00", files=2, pages=10, ocr=100.0, check=20.0),
        _run("s1", f"{today}T10:00:00", files=2, pages=10, ocr=0.0, check=30.0),
        _run("s2", f"{today}T11:00:00", files=1, pages=5, ocr=25.0, check=25.0),
    )
    out = audit_store.technical_metrics(days=30)
    ids = [s["session_id"] for s in out["sessions"]]
    assert ids == ["s2", "s1"], "phiên mới nhất đứng đầu, mỗi phiên đúng MỘT dòng"

    s1 = out["sessions"][1]
    assert (s1["runs"], s1["files"], s1["pages"]) == (2, 4, 20)
    assert s1["ocr_seconds"] == 100.0 and s1["check_seconds"] == 50.0
    # Đơn giá tính trên TỔNG hai bước: (100 + 50) / 20 trang.
    assert s1["seconds_per_page"] == 7.5
    assert s1["seconds_per_file"] == 37.5
    assert out["total"]["runs"] == 3 and out["total"]["pages"] == 25


def test_chi_so_ky_thuat_ban_ghi_cu_khong_bien_mat_va_khong_bia_don_gia():
    """Bản ghi cũ không có `total_pages`. Phiên vẫn phải hiện (nếu không trông như
    hệ thống ngừng chạy), nhưng đơn giá mỗi trang là None — 0 s/trang là nói dối."""
    today = datetime.now().date().isoformat()
    _seed_records({"session_id": "cu", "ts": f"{today}T08:00:00",
                   "num_documents": 3, "documents": []})
    (s,) = audit_store.technical_metrics(days=30)["sessions"]
    assert s["pages"] == 0 and s["seconds_per_page"] is None
    assert s["seconds_per_file"] == 0.0, "có số file thì vẫn tính được, dù bằng 0 giây"


def test_anh_chup_nhat_ky_khong_giu_du_lieu_cu_sau_khi_ghi_them():
    """Ảnh chụp nhật ký chỉ được dùng lại khi tệp KHÔNG đổi.

    Một lượt mở trang Chỉ số kỹ thuật duyệt nhật ký ba lần (tổng hợp · phân vị · chất
    lượng) nên phải có ảnh chụp; nhưng ảnh chụp giữ quá lâu thì lượt kiểm tra vừa xong
    không hiện ra, và đó là lỗi im lặng — số cũ trông vẫn hợp lệ."""
    today = datetime.now().date().isoformat()
    _seed_records(_run("s1", f"{today}T08:00:00"))
    assert len(audit_store.technical_metrics(days=30)["sessions"]) == 1

    with open(audit_store.AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(_run("s2", f"{today}T09:00:00"), ensure_ascii=False) + "\n")
    assert len(audit_store.technical_metrics(days=30)["sessions"]) == 2, \
        "ảnh chụp nhật ký giữ dữ liệu cũ sau khi có lượt mới"


def test_chi_so_ky_thuat_loai_phien_ngoai_cua_so_ngay():
    old = (datetime.now().date() - timedelta(days=40)).isoformat()
    today = datetime.now().date().isoformat()
    _seed_records(_run("cu", f"{old}T08:00:00"), _run("moi", f"{today}T08:00:00"))
    out = audit_store.technical_metrics(days=30)
    assert [s["session_id"] for s in out["sessions"]] == ["moi"]


def test_dong_nhat_ky_hong_khong_lam_sap_api(client):
    """Một dòng JSONL hỏng không được kéo sập cả trang Thống kê."""
    audit_store.AUDIT_FILE.write_text(
        "{khong phai json}\n"
        + json.dumps({"session_id": "s1", "documents": []}) + "\n",
        encoding="utf-8")
    assert client.get("/api/v1/stats").status_code == 200
    assert len(client.get("/api/v1/audit").json()["records"]) == 1


# ===========================================================================
# 9) META — health, danh sách bộ trường, DPI
# ===========================================================================
def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_danh_sach_bo_truong_co_bo_mac_dinh(client):
    items = client.get("/api/v1/field-sets").json()["field_sets"]
    mau = next(i for i in items if i["id"] == FIELD_SET)
    assert mau["source"] == "default"
    assert mau["fields"] == len(config_store.load_field_set(FIELD_SET)["fields_catalog"])


# Biên đọc TỪ `dpi_bounds()` chứ không viết cứng: bảng biên nằm ở app/data/settings.json
# — test bám vào nguồn đó thì đổi bảng không phải sửa test.
_DPI_LO, _DPI_HI = meta.dpi_bounds()


@pytest.mark.parametrize("sent,expect", [(50, _DPI_LO), (10_000, _DPI_HI), (200, 200)])
def test_dpi_bi_kep_trong_bien(client, monkeypatch, sent, expect):
    monkeypatch.setattr(core.settings, "ocr_dpi", 220)
    assert client.post("/api/v1/settings/ocr-dpi", json={"value": sent}).json()["value"] == expect


def test_dpi_khong_phai_so_tra_400(client):
    assert client.post("/api/v1/settings/ocr-dpi", json={"value": "ba tram"}).status_code == 400
    assert client.post("/api/v1/settings/ocr-dpi", json={}).status_code == 400


def test_dpi_doi_thi_so_o_anh_doi_theo_bac(client, monkeypatch):
    """DPI kéo theo số ô ảnh Vintern + số đoạn luật theo đúng bảng bậc."""
    monkeypatch.setattr(core.settings, "ocr_dpi", 150)
    monkeypatch.setattr(core.settings, "vintern_max_tiles", 1)
    body = client.post("/api/v1/settings/ocr-dpi", json={"value": _DPI_HI}).json()
    tier = meta.tier_for_dpi(_DPI_HI)
    assert body["value"] == _DPI_HI
    assert body["max_tiles"] == tier["vintern_max_tiles"] == core.settings.vintern_max_tiles
    assert body["rag_total_cap"] == tier["rag_total_cap"]


def test_dpi_get_va_post_tra_cung_hinh_dang(client):
    """GET và POST cùng đi qua `_dpi_state` -> KHÔNG được lệch khóa nào.

    Hai endpoint từng dựng dict riêng, nên thêm khóa mới dễ chỉ sửa một bên."""
    post = client.post("/api/v1/settings/ocr-dpi", json={"value": 220}).json()
    get = client.get("/api/v1/settings/ocr-dpi").json()
    assert set(post) == set(get) == {
        "value", "min", "max", "max_tiles", "rag_total_cap",
        "tier_label", "tier_label_en",
    }
    assert post == get


def test_bac_dpi_du_phong_lay_bac_CAO_nhat(monkeypatch):
    """REGRESSION: vòng lặp duyệt bản ĐÃ SẮP XẾP nhưng nhánh dự phòng lại lấy
    `tiers[-1]` của danh sách GỐC. Bảng `dpi_tiers` khai lệch thứ tự trong
    settings.json là DPI cao nhất rơi vào bậc THẤP nhất — ảnh nét nhất cắt ít ô nhất,
    đúng ngược ý bảng bậc."""
    monkeypatch.setattr(meta, "_ocr_cfg", lambda: {"dpi_tiers": [
        {"max_dpi": 301, "vintern_max_tiles": 12, "rag_total_cap": 16},
        {"max_dpi": 200, "vintern_max_tiles": 4, "rag_total_cap": 8},
    ]})
    assert meta.tier_for_dpi(150)["vintern_max_tiles"] == 4
    assert meta.tier_for_dpi(500)["vintern_max_tiles"] == 12


# ===========================================================================
# 10) BỘ TRƯỜNG CỦA NGƯỜI DÙNG — vòng đời tạo · đọc · xóa, kiểm hợp lệ
# ===========================================================================
FS_CFG = {
    "display_name": "Bộ trường test",
    "signed_date_field": "ngay_ky",
    "fields_catalog": {
        "ngay_ky": {"label": "Ngày ký", "value_type": "date", "check_type": "declaration"},
        "noi_dung": {"label": "Nội dung chính"},
    },
    "field_check_mode": {"always_check": ["noi_dung"]},
}


def _put(client, fid: str, cfg=FS_CFG):
    content = cfg if isinstance(cfg, str) else json.dumps(cfg, ensure_ascii=False)
    return client.put("/api/v1/config/field-set", json={"id": fid, "content": content})


def test_vong_doi_bo_truong_nguoi_dung(client):
    assert _put(client, "test_set").json()["id"] == "test_set"

    items = client.get("/api/v1/config/field-sets").json()["field_sets"]
    mine = next(i for i in items if i["id"] == "test_set")
    assert (mine["source"], mine["display_name"], mine["fields"]) == ("user", "Bộ trường test", 2)
    # Dùng được NGAY ở trang tải lên.
    assert "test_set" in {i["id"] for i in client.get("/api/v1/field-sets").json()["field_sets"]}

    r = client.get("/api/v1/config/field-set", params={"id": "test_set"}).json()
    assert r["source"] == "user"
    assert json.loads(r["content"])["fields_catalog"].keys() == FS_CFG["fields_catalog"].keys()

    assert client.delete("/api/v1/config/field-set", params={"id": "test_set"}).status_code == 200
    assert client.get("/api/v1/config/field-set", params={"id": "test_set"}).status_code == 404
    assert "test_set" not in {i["id"] for i in client.get("/api/v1/config/field-sets").json()["field_sets"]}


def test_ghi_de_bo_truong_da_co(client):
    _put(client, "test_set")
    assert _put(client, "test_set", dict(FS_CFG, display_name="Tên mới")).status_code == 200
    assert config_store.load_field_set("test_set")["display_name"] == "Tên mới"
    assert config_store.user_field_set_count() == 1


def test_ma_lay_tu_noi_dung_khi_body_khong_co_id(client):
    r = client.put("/api/v1/config/field-set",
                   json={"content": json.dumps(dict(FS_CFG, id="tu_noi_dung"))})
    assert r.json()["id"] == "tu_noi_dung"
    # `id` là tên tệp, không phải nội dung -> không lưu lặp vào JSON.
    assert "id" not in json.loads(config_store.read_user_field_set("tu_noi_dung"))


def test_doc_bo_truong_mac_dinh_de_sao_chep(client):
    r = client.get("/api/v1/config/field-set", params={"id": FIELD_SET})
    assert r.status_code == 200 and r.json()["source"] == "default"
    data = json.loads(r.json()["content"])
    assert "id" not in data and data["signed_date_field"] == "ngay_ky"
    assert not config_store.field_set_problems(data)


@pytest.mark.parametrize("raw,expect", [
    ("../../etc", "etc"),
    ("Test Set!", "test_set"),
    ("a" * 200, "a" * 64),
])
def test_ma_bo_truong_duoc_lam_sach(client, raw, expect):
    """Mã bộ trường thành TÊN FILE -> phải lọc sạch trước khi chạm đĩa."""
    assert _put(client, raw).json()["id"] == expect


@pytest.mark.parametrize("bad_id", ["..", "///", "___"])
def test_ma_bo_truong_rong_sau_khi_lam_sach_tra_400(client, bad_id):
    assert _put(client, bad_id).status_code == 400


@pytest.mark.parametrize("fid", [FIELD_SET, FIELD_SET.upper()])
def test_khong_ghi_de_duoc_bo_truong_mac_dinh(client, fid):
    """Bộ mặc định chỉ sửa qua trang Quản trị (có mã) — router này thì không."""
    r = _put(client, fid)
    assert r.status_code == 400 and "mặc định" in r.json()["detail"]
    assert config_store.user_field_set_count() == 0


def test_xoa_bo_truong_mac_dinh_qua_config_tra_404(client):
    assert client.delete("/api/v1/config/field-set", params={"id": FIELD_SET}).status_code == 404
    assert config_store.load_field_set(FIELD_SET)["fields_catalog"]


def test_template_la_bo_truong_hop_le(client):
    """Khung mẫu đổ sẵn vào ô soạn phải tự qua được kiểm hợp lệ — mẫu hỏng là người
    dùng nhận lỗi ngay khi bấm Lưu lần đầu."""
    content = client.get("/api/v1/config/template").json()["content"]
    assert config_store.field_set_problems(json.loads(content)) == []
    assert _put(client, "tu_mau", content).status_code == 200


def _fc(**entry) -> str:
    return json.dumps({"display_name": "X", "fields_catalog": {"k": {"label": "L", **entry}}})


@pytest.mark.parametrize("content,thieu", [
    ("{khong phai json}", "JSON"),
    (json.dumps([1, 2, 3]), "đối tượng JSON"),
    (json.dumps({"display_name": "x"}), "fields_catalog"),
    (json.dumps({"display_name": "x", "fields_catalog": {}}), "fields_catalog"),
    (json.dumps({"fields_catalog": {"k": {"label": "L"}}}), "display_name"),
    (json.dumps({"display_name": "x", "fields_catalog": {"k": {}}}), "label"),
    (json.dumps({"display_name": "x", "fields_catalog": {"a b": {"label": "L"}}}), "Mã trường"),
    (_fc(value_type="ngay"), "value_type"),
    (_fc(check_type="tuy_y"), "check_type"),
    (json.dumps({"display_name": "x", "fields_catalog": {"k": {"label": "L"}},
                 "field_check_mode": {"always_check": ["khong_co"]}}), "always_check"),
    (json.dumps({"display_name": "x", "fields_catalog": {"k": {"label": "L"}},
                 "signed_date_field": "k"}), "signed_date_field"),
])
def test_bo_truong_khong_hop_le_tra_400_va_khong_ghi(client, content, thieu):
    r = _put(client, "x", content)
    assert r.status_code == 400
    assert thieu in r.json()["detail"]
    assert config_store.read_user_field_set("x") is None


@pytest.mark.parametrize("content,expect", [
    ("{dang go do", {"ok": False, "json_error": True, "problems": []}),
    (json.dumps(FS_CFG), {"ok": True, "json_error": False, "problems": []}),
])
def test_validate_soi_noi_dung_dang_soan(client, content, expect):
    assert client.post("/api/v1/config/validate", json={"content": content}).json() == expect


def test_validate_bao_loi_ma_khong_luu_gi(client):
    r = client.post("/api/v1/config/validate", json={"content": _fc(value_type="ngay")}).json()
    assert r["ok"] is False and r["json_error"] is False
    assert any("value_type" in p for p in r["problems"])
    assert config_store.user_field_set_count() == 0


def test_doc_xoa_bo_truong_khong_ton_tai_tra_404(client):
    params = {"id": "khong_co"}
    assert client.get("/api/v1/config/field-set", params=params).status_code == 404
    assert client.delete("/api/v1/config/field-set", params=params).status_code == 404


def test_bo_truong_hong_tren_dia_van_hien_kem_loi(client):
    """Tệp bị sửa tay hỏng vẫn phải hiện trong danh sách (kèm `error`) — biến mất lặng
    lẽ thì người dùng không biết vì sao không chọn được."""
    config_store.write_user_field_set("hong", "{khong phai json")
    item = next(i for i in client.get("/api/v1/config/field-sets").json()["field_sets"]
                if i["id"] == "hong")
    assert item["source"] == "user" and "error" in item
    r = client.post("/api/v1/sessions", data={"field_set": "hong"}, files=[_pdf("a.pdf")])
    assert r.status_code == 400


# ===========================================================================
# 11) XUẤT PDF
# ===========================================================================
def test_xuat_pdf_khi_chua_co_bao_cao_tra_404(client, tmp_path):
    sid = _seed_session(tmp_path, documents={"documents": []})
    assert client.get(f"/api/v1/sessions/{sid}/export.pdf").status_code == 404


# ===========================================================================
# 12) KHÓA TIẾN ĐỘ CỦA BƯỚC KIỂM TRA
# ===========================================================================
@pytest.fixture()
def fake_validate(monkeypatch):
    """Thay chuỗi kiểm tra (gộp tài liệu -> RAG -> LLM -> nhật ký) bằng bản giả: test
    này kiểm ĐÚNG MỘT THỨ — tiến độ được ghi dưới khóa nào."""
    async def _report(*_a, **_k):
        return {"field_set_id": "", "field_set_name": "", "documents": [], "_meta": {}}

    monkeypatch.setattr(sessions_router, "merge_for_check",
                        lambda _docs: {"contract": {"contract_meta": {}}})
    monkeypatch.setattr(sessions_router, "job_prompt_of", lambda _c: {})
    monkeypatch.setattr(sessions_router, "record_run", lambda **_k: None)
    monkeypatch.setattr(sessions_router, "build_report", _report)


@pytest.fixture()
def clean_progress():
    pg._PROGRESS.clear()
    yield pg._PROGRESS
    pg._PROGRESS.clear()


def test_kiem_tra_ghi_tien_do_theo_khoa_cua_luot(client, tmp_path, fake_validate, clean_progress):
    """REGRESSION: bước kiểm tra KHÔNG được dùng session_id làm khóa SSE.

    Mốc "done" của lượt trước sống trong registry tới `_TTL_SECONDS` (1 giờ), còn
    client mở SSE TRƯỚC khi POST tới nơi. Dùng chung khóa thì lượt "Kiểm tra lại" đọc
    trúng mốc kết thúc CŨ ngay khung đầu tiên, đóng luồng, rồi chạy vài phút với thanh
    tiến độ đứng yên — hồ sơ vẫn ra kết quả nên không log nào kêu."""
    sid = _seed_session(tmp_path, documents={"documents": []})
    pg.progress_update(sid, "done")                      # dấu vết của LƯỢT TRƯỚC
    cu = dict(clean_progress[sid])

    r = client.post(f"/api/v1/sessions/{sid}/validate",
                    json={"documents": [], "progress_id": "pid-luot-2"})

    assert r.status_code == 200
    assert clean_progress["pid-luot-2"]["stage"] == "done"
    assert clean_progress[sid] == cu, "khóa cũ phải nguyên vẹn — lượt mới có khóa riêng"


def test_client_cu_khong_gui_progress_id_van_lui_ve_session_id(
        client, tmp_path, fake_validate, clean_progress):
    """Tương thích ngược: client chưa cập nhật vẫn phải nhận được tiến độ."""
    sid = _seed_session(tmp_path, documents={"documents": []})
    assert client.post(f"/api/v1/sessions/{sid}/validate", json={"documents": []}).status_code == 200
    assert clean_progress[sid]["stage"] == "done"


def test_kiem_tra_khi_bo_truong_da_bi_xoa_tra_400(client, tmp_path):
    """Phiên tải lên bằng bộ trường đã bị xóa: báo rõ cho người dùng (400), không 500."""
    docs = _doc_store()
    docs["documents"][0]["contract"]["contract_meta"]["field_set_id"] = "da_bi_xoa"
    sid = _seed_session(tmp_path, documents=docs)
    r = client.post(f"/api/v1/sessions/{sid}/validate", json={"documents": []})
    assert r.status_code == 400
    assert "Bộ trường" in r.json()["detail"]


# ===========================================================================
# 13) NGÀY KÝ — MỘT đường sửa duy nhất, và nó phải tới được bộ lọc quy định
# ===========================================================================
def _doc_ngay_ky(value=None) -> dict:
    return {"documents": [{
        "doc_id": "doc1", "source_file": "hd.pdf",
        "contract": {
            "extracted_fields": {"ngay_ky": {"value": value, "confidence": 1.0 if value else 0.0,
                                             "value_type": "date"}},
            "contract_meta": {"field_set_id": FIELD_SET, "signed_date_field": "ngay_ky"},
        },
    }]}


def _contract(sid: str) -> dict:
    return read_json(get_paths(sid).documents_json)["documents"][0]["contract"]


@pytest.mark.parametrize("nhap,iso", [
    ("2025-03-01", "2025-03-01"),
    ("01/03/2025", "2025-03-01"),
    ("ngày 01 tháng 3 năm 2025", "2025-03-01"),
])
def test_sua_ngay_ky_dong_bo_sang_derived(client, tmp_path, nhap, iso):
    """REGRESSION: bộ lọc hiệu lực văn bản đọc ngày ký qua `signed_date_of`, còn bước
    gộp tài liệu dùng cả `derived.signed_date`. Không đồng bộ thì người duyệt sửa ngày ký
    xong, màn hình hiện ngày mới còn bộ lọc vẫn chạy trên ngày cũ — sai cả bộ căn cứ
    pháp lý mà không có dấu hiệu nào. Giá trị cũng phải NẮN VỀ ISO: '01/03/2025' để
    nguyên thì `date_to_int` gom chữ số ra 1032025, một mốc vô nghĩa."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky())
    assert _patch(client, sid, {"ngay_ky": nhap}).status_code == 200

    contract = _contract(sid)
    assert contract["derived"]["signed_date"] == {
        "value": iso, "confidence": 1.0, "from_field": "ngay_ky"}
    assert contract["extracted_fields"]["ngay_ky"]["value"] == iso
    assert signed_date_of(contract) == iso


def test_xoa_ngay_ky_thi_go_luon_o_derived(client, tmp_path):
    """Xóa trắng ngày ký phải xóa ở CẢ hai chỗ, nếu không bộ lọc vẫn ôm ngày cũ."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky())
    _patch(client, sid, {"ngay_ky": "2025-03-01"})
    _patch(client, sid, {"ngay_ky": None})
    contract = _contract(sid)
    assert contract["derived"]["signed_date"]["value"] is None
    assert signed_date_of(contract) == ""


def test_validate_khong_con_nhan_signed_date_override(client, tmp_path, fake_validate,
                                                      clean_progress):
    """Đường nhập ngày ký thứ hai đã bị gỡ: gửi kèm cũng không được dựng thành trường.

    Hai đường sửa cho cùng một dữ kiện là hai chỗ để giá trị lệch nhau — mà dữ kiện
    này lại quyết định BỘ VĂN BẢN LUẬT đem ra đối chiếu."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky())
    r = client.post(f"/api/v1/sessions/{sid}/validate",
                    json={"documents": [], "signed_date_override": "1999-01-01"})
    assert r.status_code == 200
    assert not hasattr(core.ValidateRequest(), "signed_date_override")


def test_ngay_ky_khong_doc_duoc_tra_400_chu_khong_ghi_bua(client, tmp_path):
    """Chuỗi không ra ngày mà vẫn ghi thì nó đi thẳng vào bộ lọc hiệu lực văn bản."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky())
    assert _patch(client, sid, {"ngay_ky": "hôm nọ"}).status_code == 400
    assert _contract(sid)["extracted_fields"]["ngay_ky"]["value"] is None


# ===========================================================================
# 14) ĐỆM: khóa phiên và báo cáo chỉ được trượt khi có thứ THẬT SỰ đổi
# ===========================================================================
def test_khoa_dem_phien_chi_doi_khi_noi_dung_hoac_bo_truong_doi():
    """REGRESSION: khóa đệm trượt là OCR lại từ đầu rồi chạy lại LLM cho ĐÚNG bộ hồ sơ
    vừa kiểm xong — 10-30 phút cho một thay đổi không tồn tại.

    Khóa băm TỪNG file rồi sắp xếp (đổi thứ tự tải lên không đổi khóa) và băm NỘI DUNG
    bộ trường (sửa nhãn một trường thì phải trích xuất lại)."""
    from app.domain.documents.intake import Selection, cache_key_of

    fs = config_store.load_field_set(FIELD_SET)
    sel = Selection(field_set_id=FIELD_SET, field_set_name="Hợp đồng (mẫu chung)", job_prompt=fs)
    a, b = b"%PDF-A", b"%PDF-B"
    key = cache_key_of([a, b], sel)

    assert key == cache_key_of([b, a], sel)
    # Tên hiển thị của lượt chọn và THỨ TỰ khóa trong JSON bộ trường không phải nội dung.
    assert key == cache_key_of([a, b], Selection(FIELD_SET, "Tên khác", fs))
    assert key == cache_key_of([a, b], Selection(FIELD_SET, "", dict(reversed(fs.items()))))
    # Vẫn phải đổi khi NỘI DUNG file, MÃ hoặc NỘI DUNG bộ trường thật sự đổi.
    assert key != cache_key_of([a, b"%PDF-C"], sel)
    assert key != cache_key_of([a, b], Selection("bo_khac", "", fs))
    fs_sua = json.loads(json.dumps(fs))
    fs_sua["fields_catalog"]["ben_a"]["label"] = "Bên giao"
    assert key != cache_key_of([a, b], Selection(FIELD_SET, "", fs_sua))


def test_sua_truong_ve_dung_gia_tri_cu_thi_KHONG_xoa_bao_cao(client, tmp_path):
    """REGRESSION: mọi lần PATCH đều xóa `final_report.json`, kể cả khi không có gì đổi.

    Mở ô nhập rồi đóng lại y nguyên là mất báo cáo, và lượt kiểm tra sau phải chạy lại
    LLM từ đầu — 7-20 phút cho một thay đổi không tồn tại."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky("2025-03-01"),
                        report={"_meta": {"req_sig": "x"}})
    paths = get_paths(sid)

    r = _patch(client, sid, {"ngay_ky": "01/03/2025"})      # cùng ngày, khác cách viết
    assert r.status_code == 200
    assert r.json()["updated"] == [] and r.json()["unchanged"] == ["ngay_ky"]
    assert read_json(paths.final_report_json)["_meta"]["req_sig"] == "x"

    # Đổi THẬT thì vẫn phải xóa báo cáo cũ.
    r = _patch(client, sid, {"ngay_ky": "02/03/2025"})
    assert r.json()["updated"] == ["ngay_ky"]
    assert client.get(f"/api/v1/sessions/{sid}/report").status_code == 404


# ===========================================================================
# BỘ KIỂM TRA ĐANG DÙNG — trang Kiểm tra không còn ô chọn loại hồ sơ
# ===========================================================================
def test_bo_kiem_tra_dang_dung_mac_dinh_va_doi(client):
    assert client.get("/api/v1/settings/active-field-set").json() == {"field_set": FIELD_SET}
    # Bộ người dùng vừa tạo được ưu tiên khi chưa chọn gì.
    client.put("/api/v1/config/field-set", json={"id": "bo_moi", "content": json.dumps(FS_CFG)})
    assert client.get("/api/v1/settings/active-field-set").json()["field_set"] == "bo_moi"
    r = client.post("/api/v1/settings/active-field-set", json={"field_set": FIELD_SET})
    assert r.json() == {"field_set": FIELD_SET}
    assert client.get("/api/v1/settings/active-field-set").json()["field_set"] == FIELD_SET
    assert client.post("/api/v1/settings/active-field-set",
                       json={"field_set": "khong_co"}).status_code == 400
    # Bộ đã nhớ bị xóa -> rơi về bộ khác dùng được, không trả mã chết.
    client.post("/api/v1/settings/active-field-set", json={"field_set": "bo_moi"})
    client.delete("/api/v1/config/field-set", params={"id": "bo_moi"})
    assert client.get("/api/v1/settings/active-field-set").json()["field_set"] == FIELD_SET


def test_tai_len_khong_gui_bo_truong_dung_bo_dang_dung(client, fake_ocr):
    r = client.post("/api/v1/sessions", files=[_pdf("hd.pdf")])
    assert r.status_code == 200, r.text
    meta_ = r.json()["documents"][0]["extracted_json"]["contract_meta"]
    assert meta_["field_set_id"] == FIELD_SET


# ===========================================================================
# VÙNG CẦN KIỂM TRA gửi kèm lượt tải lên
# ===========================================================================
def test_vung_kiem_tra_chuyen_toi_tung_file_va_vao_khoa_dem(client, monkeypatch):
    seen: list = []

    async def _process(session_id, _data, filename, job_prompt, **k):
        seen.append(k.get("region"))
        cj, _ = extract_contract_json(session_id, filename, VAN_BAN, VAN_BAN, job_prompt)
        return {"source_file": filename, "ocr": {}, "contract": cj,
                "missing_fields": cj["missing_fields"]}

    monkeypatch.setattr(intake, "process_file", _process)
    monkeypatch.setattr(sessions_router, "prefetch_field_set_regulations", lambda _jp: None)
    regions = json.dumps([{"skip": [2], "rects": {"0": [0.5, 0.6, 0.1, 0.2]}}, None])
    r = client.post("/api/v1/sessions", data=FORM | {"regions": regions},
                    files=[_pdf("hd.pdf"), _pdf("pl.pdf", 9)])
    assert r.status_code == 200, r.text
    # Hai góc được sắp lại; file không khoanh -> None.
    assert seen == [{"skip": [2], "rects": {0: (0.1, 0.2, 0.5, 0.6)}}, None]
    # Cùng file, KHÁC vùng -> không dùng lại phiên cũ.
    sid = r.json()["session_id"]
    r2 = client.post("/api/v1/sessions", data=FORM, files=[_pdf("hd.pdf"), _pdf("pl.pdf", 9)])
    assert r2.json()["session_id"] != sid
    assert client.post("/api/v1/sessions", data=FORM | {"regions": "{hong"},
                       files=[_pdf("hd.pdf")]).status_code == 400


# ===========================================================================
# BỘ QUY ĐỊNH — nạp từ trang Kiểm tra
# ===========================================================================
@pytest.fixture()
def rules_tmp(tmp_path, monkeypatch):
    from app.domain.regulations import corpus

    d = tmp_path / "rules"
    d.mkdir()
    monkeypatch.setattr(corpus, "RULES_DIR", d)
    monkeypatch.setattr(corpus, "REGISTRY_FILE", d / "corpus.json")
    import sys

    seed_mod = sys.modules["app.domain.regulations.seed"]
    calls: list = []
    monkeypatch.setattr(seed_mod, "seed", lambda *a, **k: calls.append(1) or {})
    return d, calls


def test_nap_bo_quy_dinh_md_docx_zip(client, rules_tmp):
    import io
    import zipfile

    from tests.test_docx2md import _docx, _p

    d, calls = rules_tmp
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("thu_muc/../../thong_tu.md", "# Thông tư mẫu\n\nĐiều 1. Nội dung")
        z.writestr("anh.png", b"x")
    files = [
        ("files", ("Nghi dinh 01.docx", _docx(_p("Điều 1. Phạm vi")), "application/octet-stream")),
        ("files", ("goi.zip", buf.getvalue(), "application/zip")),
        ("files", ("hong.pdf", b"khong phai pdf", "application/pdf")),
    ]
    r = client.post("/api/v1/config/regulation-sets", data={"name": "  Bộ hợp đồng  "},
                    files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["set"] == "Bộ hợp đồng" and body["reseeded"] and calls
    saved = sorted(a["saved_as"] for a in body["added"])
    assert saved == ["nghi_dinh_01.md", "thong_tu.md"]
    # Tên trong .zip chỉ lấy phần tên tệp: không ghi được ra ngoài thư mục quy định.
    assert sorted(p.name for p in d.iterdir()) == ["corpus.json", "nghi_dinh_01.md", "thong_tu.md"]
    errs = {s["file"]: s["error"] for s in body["skipped"]}
    assert "goi.zip/anh.png" in errs and "hong.pdf" in errs
    sets = client.get("/api/v1/config/regulation-sets").json()["regulation_sets"]
    assert [(x["name"], x["documents"], sorted(x["files"])) for x in sets] == [
        ("Bộ hợp đồng", 2, ["nghi_dinh_01.md", "thong_tu.md"])]
    # Nạp lần nữa cùng tên tệp -> không ghi đè, đặt tên mới.
    r = client.post("/api/v1/config/regulation-sets", data={"name": "Bộ 2"},
                    files=[("files", ("thong_tu.md", b"# B\n\nx", "text/markdown"))])
    assert r.json()["added"][0]["saved_as"] == "thong_tu_2.md"


@pytest.mark.parametrize("name,files,code", [
    ("", [("files", ("a.md", b"# A", "text/markdown"))], 400),
    ("Bộ", [("files", ("a.exe", b"MZ", "application/octet-stream"))], 400),
])
def test_nap_bo_quy_dinh_loi(client, rules_tmp, name, files, code):
    assert client.post("/api/v1/config/regulation-sets", data={"name": name},
                       files=files).status_code == code


def test_nap_bo_quy_dinh_tep_12mb_khong_bi_413(client, rules_tmp):
    """Hồi quy: middleware từng chặn MỌI tuyến ngoài /sessions ở 4 MB, nên tệp quy định
    12057 KB nhận 413 dù router cho phép tới 200 MB."""
    big = ("# Luật lớn\n\n" + "Điều 1. Nội dung quy định.\n" * 460_000).encode()
    assert len(big) > 12057 * 1024
    r = client.post("/api/v1/config/regulation-sets", data={"name": "Bộ lớn"},
                    files=[("files", ("luat_lon.md", big, "text/markdown"))])
    assert r.status_code == 200, r.text[:200]
    assert r.json()["added"][0]["saved_as"] == "luat_lon.md"


def test_nap_bo_quy_dinh_vuot_tran_tra_413_kem_so_mb(client):
    from app.main import body_limit_of

    limit = body_limit_of("/api/v1/config/regulation-sets")
    r = client.post("/api/v1/config/regulation-sets", content=b"x",
                    headers={"content-type": "multipart/form-data; boundary=x",
                             "content-length": str(limit + 1)})
    assert r.status_code == 413 and "200 MB" in r.json()["detail"]


def test_nap_bo_quy_dinh_trung_noi_dung_bi_bo_qua(client, rules_tmp):
    """Cùng văn bản nạp hai lần vào một bộ -> không nhân đôi đoạn luật trong kho."""
    f = [("files", ("a.md", "# A\n\nĐiều 1. Nội dung.".encode(), "text/markdown"))]
    assert client.post("/api/v1/config/regulation-sets", data={"name": "Bộ"}, files=f).status_code == 200
    r = client.post("/api/v1/config/regulation-sets", data={"name": "Bộ"}, files=f)
    assert r.status_code == 400 and "không có gì mới" in r.json()["detail"]
    # Bộ KHÁC vẫn nhận cùng văn bản.
    assert client.post("/api/v1/config/regulation-sets", data={"name": "Bộ khác"},
                       files=f).status_code == 200


# ===========================================================================
# CHỐNG CSRF — request ghi từ trang web lạ
# ===========================================================================
@pytest.mark.parametrize("origin,code", [
    ("https://evil.example", 403),
    ("null", 403),
    ("http://localhost.evil.example", 403),
    ("http://localhost:5173", 200),
    ("http://127.0.0.1:8123", 200),
    ("http://localhost", 200),          # cùng origin với base_url của TestClient
])
def test_origin_la_bi_chan_khi_ghi(client, origin, code):
    r = client.post("/api/v1/config/validate", json={"content": "{}"}, headers={"Origin": origin})
    assert r.status_code == code, r.text


def test_multipart_tu_trang_la_khong_toi_duoc_router(client, monkeypatch):
    """POST multipart là 'simple request' — trình duyệt gửi không cần preflight, CORS
    không chặn được. Lớp Origin phải chặn trước khi tới router nạp bộ quy định."""
    import app.domain.regulations.upload as up

    monkeypatch.setattr(up, "add_regulation_set", lambda *a, **k: pytest.fail("đã tới router"))
    r = client.post("/api/v1/config/regulation-sets", data={"name": "x"},
                    files=[("files", ("a.md", b"# a", "text/markdown"))],
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_khong_origin_va_get_van_qua(client):
    """Công cụ dòng lệnh không gửi Origin; GET không đổi trạng thái -> không chặn."""
    assert client.post("/api/v1/config/validate", json={"content": "{}"}).status_code == 200
    assert client.get("/api/v1/config/field-sets", headers={"Origin": "https://evil.example"}).status_code == 200
    r = client.post("/api/v1/config/validate", json={"content": "{}"},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


# ===========================================================================
# SOẠN BỘ KIỂM TRA TỪ MÔ TẢ BẰNG LỜI
# ===========================================================================
_MO_TA = """Kiểm tra hợp đồng lao động gồm:
- Tên người lao động
- Ngày ký hợp đồng
- Mức lương không thấp hơn lương tối thiểu vùng
- Thời gian thử việc không quá 60 ngày
- Số lượng bản phải là số nguyên dương
- Hợp đồng phải có điều khoản bảo hiểm xã hội"""


def test_draft_theo_quy_tac_ra_bo_truong_hop_le(client):
    r = client.post("/api/v1/config/draft", json={"text": _MO_TA, "use_llm": False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "rules" and d["document_kind"] == "hợp đồng lao động"
    by = {f["label"]: f for f in d["fields"]}
    assert by["Tên người lao động"]["check_type"] == "declaration"
    assert by["Ngày ký hợp đồng"]["value_type"] == "date" and by["Ngày ký hợp đồng"]["is_signed_date"]
    assert by["Mức lương"]["value_type"] == "money" and by["Mức lương"]["check_type"] == "regulated"
    assert "60 ngày" in by["Thời gian thử việc"]["check_aspect"]
    assert by["Số lượng bản"]["check_type"] == "positive_integer"
    assert "Điều khoản bảo hiểm xã hội" in by
    # Đổ thẳng thành fields_catalog -> phải qua được bộ kiểm hợp lệ khi lưu.
    fc = {f"t{i}": {k: f[k] for k in ("label", "value_type", "check_type", "check_aspect")}
          for i, f in enumerate(d["fields"])}
    assert config_store.field_set_problems({"display_name": "X", "fields_catalog": fc}) == []


def test_draft_mo_hinh_loi_thi_roi_ve_quy_tac(client, monkeypatch):
    from app.domain.compliance import drafting

    async def _hong(_text):
        raise RuntimeError("Ollama tắt")

    monkeypatch.setattr(drafting, "_draft_by_llm", _hong)
    d = client.post("/api/v1/config/draft", json={"text": _MO_TA}).json()
    assert d["source"] == "rules" and d["fields"] and "chưa sẵn sàng" in d["note"]


def test_draft_mo_hinh_tra_ve_duoc_lam_sach(client, monkeypatch):
    from app.domain.compliance import drafting

    async def _llm(_text):
        return drafting.sanitize({"document_kind": "hợp đồng", "fields": [
            {"label": "Mức lương", "value_type": "money", "check_type": "regulated",
             "check_aspect": "≥ lương tối thiểu", "label_alts": ["Tiền lương", "mức lương"]},
            {"label": "mức lương", "value_type": "money", "check_type": "regulated"},   # trùng
            {"label": "Ngày ký", "value_type": "bogus", "check_type": "??"},             # kiểu lạ
            {"label": "Tiền đặt cọc", "value_type": "number", "check_type": "regulated"},
            "rác",
        ]})

    monkeypatch.setattr(drafting, "_draft_by_llm", _llm)
    d = client.post("/api/v1/config/draft", json={"text": "mức lương"}).json()
    assert d["source"] == "llm"
    assert [f["label"] for f in d["fields"]] == ["Mức lương", "Ngày ký", "Tiền đặt cọc"]
    assert d["fields"][2]["value_type"] == "money"
    assert d["fields"][0]["label_alts"] == ["Tiền lương"]
    assert d["fields"][1]["value_type"] == "text" and d["fields"][1]["check_type"] == "regulated"


@pytest.mark.parametrize("text", ["", "   ", "x" * 6001])
def test_draft_mo_ta_rong_hoac_qua_dai_tra_400(client, text):
    assert client.post("/api/v1/config/draft", json={"text": text}).status_code == 400
