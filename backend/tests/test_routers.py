"""Test TẦNG API (routers) — xác thực quản trị, chặn path traversal, hạn mức tải lên,
kẹp tham số truy vấn, vòng đời cấu hình người dùng, thống kê và chỉ số kỹ thuật.

Nguyên tắc của file này:
  · KHÔNG chạm dữ liệu thật — temp/, audit.jsonl và data/user_config/ đều bị trỏ sang
    thư mục tạm của pytest (fixture `isolate_store`, autouse).
  · KHÔNG gọi LLM/OCR/ChromaDB — chỉ kiểm phần router tự quyết định được.
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
from app.main import app
from app.routers import meta
from app.routers import sessions as sessions_router
from app.store import audit as audit_store
from app.store import config as config_store
from app.store import get_paths, read_json

ADMIN_TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
# Thị trường có thật trong markets.json — dùng cho các ca cần qua `resolve_selection`.
MARKET = "nhat_ban"


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Mọi đường ghi của tầng API trỏ vào tmp_path. Thiếu fixture này thì chạy test
    một lần là xóa sạch nhật ký kiểm tra thật của người dùng."""
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(core.settings, "admin_token", ADMIN_TOKEN)
    monkeypatch.setattr(audit_store, "AUDIT_FILE", tmp_path / "audit.jsonl")

    ucfg = tmp_path / "user_config"
    monkeypatch.setattr(config_store, "USER_CONFIG_DIR", ucfg)
    monkeypatch.setattr(config_store, "APPLIED_FILE", ucfg / "applied.json")
    # `routers/config.py` giữ THAM CHIẾU tới chính dict này (`from app.store import
    # USER_CONFIG_DIRS`), nên phải sửa TẠI CHỖ — gán dict mới chỉ đổi được một bên.
    for kind in list(config_store.USER_CONFIG_DIRS):
        monkeypatch.setitem(config_store.USER_CONFIG_DIRS, kind, ucfg / kind)


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
        json.dumps({"documents": [{"doc_id": "BI_RO_RI", "source_file": "mat.pdf"}],
                    "dossier": {}}), encoding="utf-8")

    r = client.get(f"/api/v1/sessions/{evil}/documents")
    assert "BI_RO_RI" not in r.text, "path traversal đọc được file ngoài temp/"
    assert r.status_code == 404


def test_session_id_hop_le_van_chay_binh_thuong(client, tmp_path):
    """Rào chặn phải HẸP: UUID thật vẫn phải đi qua, nếu không là chặn nhầm cả hệ."""
    sid = _seed_session(tmp_path, documents={"documents": [], "dossier": {}})
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
    assert isinstance(r.json()["files"], list)


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
# 3) ADMIN — chặn path traversal ở tham số `rel`
# ===========================================================================
@pytest.mark.parametrize("rel", [
    "rules/../../../../etc/passwd",
    "rules/..\\..\\x.md",
    "khong_co_nhom.md",          # thiếu dấu '/' -> không phân giải được nhóm
    "linh_tinh/x.md",            # nhóm không tồn tại
    "markets/nhat_ban.json",     # nhóm 'markets' CHỈ cho sửa đúng markets.json
    "rules/x.exe",               # đuôi ngoài .json/.md
])
def test_admin_rel_khong_hop_le_bi_chan(client, rel):
    assert client.get("/api/v1/admin/file", params={"rel": rel},
                      headers=AUTH).status_code == 400


def test_admin_ghi_file_json_hong_bi_tu_choi_truoc_khi_ghi(client):
    """JSON sai cú pháp phải bị chặn TRƯỚC khi chạm đĩa — nếu không, một dấu phẩy
    thừa làm hỏng cấu hình mặc định của cả hệ."""
    r = client.put("/api/v1/admin/file", headers=AUTH,
                   json={"rel": "markets/markets.json", "content": "{ khong-phai-json"})
    assert r.status_code == 400
    assert "JSON" in r.json()["detail"]


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
# 5) HẠN MỨC TẢI LÊN
# ===========================================================================
def _pdf(name: str, size: int = 8) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, b"%PDF-" + b"0" * size, "application/pdf"))


def test_thieu_hoan_toan_truong_files_tra_422(client):
    """`files: list[UploadFile] = File(...)` là BẮT BUỘC nên Pydantic chặn ở 422
    TRƯỚC khi vào thân hàm — nhánh `if not files -> 400` trong router không bao giờ
    chạy được qua HTTP (multipart không diễn đạt được 'danh sách rỗng')."""
    assert client.post("/api/v1/sessions", data={"market": MARKET}).status_code == 422


def test_vuot_so_file_toi_da_tra_413(client, monkeypatch):
    monkeypatch.setattr(sessions_router, "MAX_FILES", 2)
    r = client.post("/api/v1/sessions", data={"market": MARKET},
                    files=[_pdf(f"f{i}.pdf") for i in range(3)])
    assert r.status_code == 413


def test_file_qua_lon_tra_413_kem_ten_file(client, monkeypatch):
    monkeypatch.setattr(sessions_router, "MAX_FILE_BYTES", 10)
    r = client.post("/api/v1/sessions", data={"market": MARKET},
                    files=[_pdf("qua_lon.pdf", size=200)])
    assert r.status_code == 413
    assert "qua_lon.pdf" in r.json()["detail"]


def test_tong_dung_luong_vuot_tra_413(client, monkeypatch):
    monkeypatch.setattr(sessions_router, "MAX_FILE_BYTES", 10_000)
    monkeypatch.setattr(sessions_router, "MAX_TOTAL_BYTES", 100)
    r = client.post("/api/v1/sessions", data={"market": MARKET},
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
    r = client.post("/api/v1/sessions", data={"market": MARKET},
                    files=[_pdf("that.pdf"),
                           ("files", ("gia.pdf", b"PK\x03\x04rac", "application/pdf"))])
    assert r.status_code == 400
    assert "gia.pdf" in r.json()["detail"]


def test_thi_truong_khong_hop_le_tra_400(client):
    r = client.post("/api/v1/sessions", data={"market": "khong-ton-tai"},
                    files=[_pdf("a.pdf")])
    assert r.status_code == 400


def test_o_khac_nhap_bua_bi_tu_choi(client):
    """market='khac' đòi ô 'thị trường khác' đủ nghĩa (is_meaningful_text)."""
    r = client.post("/api/v1/sessions",
                    data={"market": "khac", "market_other": "aaaa"},
                    files=[_pdf("a.pdf")])
    assert r.status_code == 400


# ===========================================================================
# 6) SỬA TAY GIÁ TRỊ TRÍCH XUẤT (PATCH .../fields)
# ===========================================================================
def _doc_store() -> dict:
    return {
        "documents": [{
            "doc_id": "doc1",
            "source_file": "a.pdf",
            "contract": {
                "extracted_fields": {
                    "tien_luong": {"label": "Tiền lương", "value": None, "confidence": 0.0},
                },
                "missing_fields": ["tien_luong"],
                "input_flags": [
                    {"code": "SALARY_RANGE", "field": "tien_luong", "block_field": True},
                ],
                "contract_meta": {},
            },
            "missing_fields": ["tien_luong"],
        }],
        "dossier": {},
    }


def test_sua_tay_dien_gia_tri_va_go_co_chan(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"tien_luong": "300 USD"}})
    assert r.status_code == 200
    assert r.json()["updated"] == ["tien_luong"]
    assert r.json()["missing_fields"] == []

    saved = json.loads((tmp_path / "temp" / sid / "documents.json").read_text(encoding="utf-8"))
    fld = saved["documents"][0]["contract"]["extracted_fields"]["tien_luong"]
    assert fld["value"] == "300 USD"
    assert fld["confidence"] == 1.0
    assert fld["evidence"]["source"] == "USER_EDIT"
    # Người dùng đã tự xác nhận -> cờ chất lượng CHẶN của đúng trường đó phải biến mất.
    assert saved["documents"][0]["contract"]["input_flags"] == []


def test_sua_tay_gia_tri_rong_dua_truong_ve_thieu(client, tmp_path):
    store = _doc_store()
    store["documents"][0]["contract"]["extracted_fields"]["tien_luong"]["value"] = "300 USD"
    store["documents"][0]["contract"]["missing_fields"] = []
    sid = _seed_session(tmp_path, documents=store)
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"tien_luong": None}})
    assert r.json()["missing_fields"] == ["tien_luong"]


def test_sua_tay_xoa_bao_cao_cu(client, tmp_path):
    """Báo cáo cũ tính trên dữ liệu cũ — không xóa thì 'kiểm tra lại' trả cache sai."""
    sid = _seed_session(tmp_path, documents=_doc_store(), report={"overall_verdict": "PASS"})
    client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                 json={"fields": {"tien_luong": "300 USD"}})
    assert not (tmp_path / "temp" / sid / "final_report.json").exists()


def test_sua_tay_truong_ngoai_catalog_bi_bo_qua(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"truong_bia_dat": "x"}})
    assert r.status_code == 400


def test_sua_tay_thieu_fields_tra_400(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    assert client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                        json={"fields": {}}).status_code == 400


def test_sua_tay_sai_doc_id_tra_404(client, tmp_path):
    sid = _seed_session(tmp_path, documents=_doc_store())
    assert client.patch(f"/api/v1/sessions/{sid}/documents/doc99/fields",
                        json={"fields": {"tien_luong": "1"}}).status_code == 404


def test_sua_thoi_han_hop_dong_quy_doi_ra_thang(client, tmp_path):
    """`__contract_duration` là khóa ĐẶC BIỆT: không nằm trong fields_catalog mà
    thuộc contract_meta, và phải tự quy đổi ra số tháng."""
    sid = _seed_session(tmp_path, documents=_doc_store())
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"__contract_duration": "3 năm"}})
    assert r.status_code == 200
    saved = json.loads((tmp_path / "temp" / sid / "documents.json").read_text(encoding="utf-8"))
    meta = saved["documents"][0]["contract"]["contract_meta"]
    assert meta["contract_duration"] == "3 năm"
    assert meta["contract_duration_months"] == 36


# ===========================================================================
# 7) THỐNG KÊ / NHẬT KÝ — kẹp tham số truy vấn
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


def test_reminders_days_bi_kep(client):
    r = client.get("/api/v1/reminders", params={"days": 10**9})
    assert r.status_code == 200
    assert r.json()["days"] == 3650
    assert client.get("/api/v1/reminders", params={"days": -1}).json()["days"] == 0


def test_audit_doc_duoc_ban_ghi_da_ghi(client):
    audit_store.AUDIT_FILE.write_text(
        json.dumps({"session_id": "s1", "market_name": "Nhật Bản", "job_type_name": "TTS",
                    "documents": [{"doc_id": "doc1", "source_file": "a.pdf",
                                   "overall_verdict": "PASS"}]}) + "\n",
        encoding="utf-8")
    assert len(client.get("/api/v1/audit").json()["records"]) == 1
    assert client.get("/api/v1/stats").json()["totals"]["PASS"] == 1


def test_audit_tim_kiem_bo_dau(client):
    audit_store.AUDIT_FILE.write_text(
        json.dumps({"session_id": "s1", "market_name": "Nhật Bản", "job_type_name": "",
                    "country_name": "", "documents": []}) + "\n", encoding="utf-8")
    assert len(client.get("/api/v1/audit", params={"q": "nhat ban"}).json()["records"]) == 1
    assert len(client.get("/api/v1/audit", params={"q": "dai loan"}).json()["records"]) == 0


def _seed_metrics(*recs: dict) -> None:
    audit_store.AUDIT_FILE.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")


def _run(sid: str, ts: str, *, files=1, pages=0, mb_bytes=0, ocr=0.0, check=0.0) -> dict:
    return {"session_id": sid, "ts": ts, "num_documents": files, "total_pages": pages,
            "total_bytes": mb_bytes, "ocr_seconds": ocr, "documents": [],
            "metrics": {"seconds": check}}


def test_chi_so_ky_thuat_gom_theo_phien_khong_theo_ngay():
    """Hai lượt kiểm tra CÙNG phiên phải thành MỘT dòng và cộng dồn chi phí.

    Gom theo ngày thì hai lượt này lẫn với hồ sơ khác chạy cùng hôm, và số 'mỗi
    trang' hết so sánh được giữa các hồ sơ."""
    today = datetime.now().date().isoformat()
    _seed_metrics(
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
    _seed_metrics({"session_id": "cu", "ts": f"{today}T08:00:00",
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
    _seed_metrics(_run("s1", f"{today}T08:00:00"))
    assert len(audit_store.technical_metrics(days=30)["sessions"]) == 1

    with open(audit_store.AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(_run("s2", f"{today}T09:00:00"), ensure_ascii=False) + "\n")
    assert len(audit_store.technical_metrics(days=30)["sessions"]) == 2, \
        "ảnh chụp nhật ký giữ dữ liệu cũ sau khi có lượt mới"


def test_chi_so_ky_thuat_loai_phien_ngoai_cua_so_ngay():
    old = (datetime.now().date() - timedelta(days=40)).isoformat()
    today = datetime.now().date().isoformat()
    _seed_metrics(_run("cu", f"{old}T08:00:00"), _run("moi", f"{today}T08:00:00"))
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
# 8) META — health, nhận diện vai trò, DPI
# ===========================================================================
def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_classify_files_tra_vai_tro_theo_ten_file(client):
    r = client.post("/api/v1/classify-files",
                    json={"filenames": ["hop dong cung ung.pdf", "abcxyz.pdf"]})
    assert r.status_code == 200
    roles = r.json()["roles"]
    assert [x["filename"] for x in roles] == ["hop dong cung ung.pdf", "abcxyz.pdf"]
    assert roles[1]["role"] == "unknown"


def test_classify_files_danh_sach_rong(client):
    assert client.post("/api/v1/classify-files", json={}).json()["roles"] == []


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


# ===========================================================================
# 9) CẤU HÌNH NGƯỜI DÙNG — vòng đời tạo · áp dụng · xóa
# ===========================================================================
JOB_CFG = {
    "job_id": "test_job",
    "display_name": "Bộ trường test",
    "fields_catalog": {"tien_luong": {"label": "Tiền lương"}},
}


def test_vong_doi_cau_hinh_nguoi_dung(client):
    body = {"kind": "jobs", "id": "test_job", "content": json.dumps(JOB_CFG)}
    assert client.put("/api/v1/config/item", json=body).json()["id"] == "test_job"

    items = client.get("/api/v1/config/items").json()
    assert [i["id"] for i in items["items"]] == ["test_job"]
    assert items["applied"]["jobs"] == []

    assert client.post("/api/v1/config/apply",
                       json={"kind": "jobs", "id": "test_job", "applied": True}).status_code == 200
    assert client.get("/api/v1/config/items").json()["applied"]["jobs"] == ["test_job"]

    # Gỡ áp dụng -> hệ quay về cấu hình mặc định, file vẫn còn.
    client.post("/api/v1/config/apply", json={"kind": "jobs", "id": "test_job", "applied": False})
    assert client.get("/api/v1/config/items").json()["applied"]["jobs"] == []

    assert client.delete("/api/v1/config/item",
                         params={"kind": "jobs", "id": "test_job"}).status_code == 200
    assert client.get("/api/v1/config/items").json()["items"] == []


def test_xoa_cau_hinh_dang_ap_dung_thi_go_ap_dung_luon(client):
    client.put("/api/v1/config/item",
               json={"kind": "jobs", "id": "test_job", "content": json.dumps(JOB_CFG)})
    client.post("/api/v1/config/apply", json={"kind": "jobs", "id": "test_job", "applied": True})
    client.delete("/api/v1/config/item", params={"kind": "jobs", "id": "test_job"})
    # Còn sót trong applied.json là hệ đi tìm một file không tồn tại ở mọi lần dựng bộ trường.
    assert client.get("/api/v1/config/items").json()["applied"]["jobs"] == []


@pytest.mark.parametrize("raw,expect", [
    ("../../etc", "etc"),
    ("Test Job!", "test_job"),
    ("a" * 200, "a" * 64),
])
def test_ma_cau_hinh_duoc_lam_sach(client, raw, expect):
    """Mã cấu hình thành TÊN FILE -> phải lọc sạch trước khi chạm đĩa."""
    cfg = dict(JOB_CFG, job_id=raw)
    r = client.put("/api/v1/config/item",
                   json={"kind": "jobs", "id": raw, "content": json.dumps(cfg)})
    assert r.json()["id"] == expect


@pytest.mark.parametrize("bad_id", ["..", "///", "___"])
def test_ma_cau_hinh_rong_sau_khi_lam_sach_tra_400(client, bad_id):
    r = client.put("/api/v1/config/item",
                   json={"kind": "jobs", "id": bad_id, "content": json.dumps(JOB_CFG)})
    assert r.status_code == 400


def test_loai_cau_hinh_khong_hop_le_tra_400(client):
    assert client.get("/api/v1/config/template", params={"kind": "linh_tinh"}).status_code == 400
    assert client.put("/api/v1/config/item",
                      json={"kind": "linh_tinh", "id": "x", "content": "{}"}).status_code == 400


def test_template_tra_json_hop_le_cho_ca_hai_loai(client):
    for kind in ("jobs", "markets"):
        content = client.get("/api/v1/config/template", params={"kind": kind}).json()["content"]
        assert isinstance(json.loads(content), dict)


@pytest.mark.parametrize("content,thieu", [
    ("{khong phai json}", "JSON"),
    (json.dumps({"display_name": "x"}), "fields_catalog"),
    (json.dumps({"fields_catalog": {"k": {}}}), "label"),
    (json.dumps({"fields_catalog": {"k": {"label": "L"}}}), "display_name"),
    (json.dumps([1, 2, 3]), "đối tượng JSON"),
])
def test_cau_hinh_jobs_thieu_khoa_bat_buoc_tra_400(client, content, thieu):
    r = client.put("/api/v1/config/item", json={"kind": "jobs", "id": "x", "content": content})
    assert r.status_code == 400
    assert thieu in r.json()["detail"]


@pytest.mark.parametrize("content,thieu", [
    (json.dumps({"job_id": "j", "job_types": [{"id": "a"}]}), "name"),
    (json.dumps({"name": "M", "job_types": [{"id": "a"}]}), "job_id"),
    (json.dumps({"name": "M", "job_id": "j"}), "job_types"),
])
def test_cau_hinh_markets_thieu_khoa_bat_buoc_tra_400(client, content, thieu):
    r = client.put("/api/v1/config/item", json={"kind": "markets", "id": "x", "content": content})
    assert r.status_code == 400
    assert thieu in r.json()["detail"]


def test_ap_dung_cau_hinh_khong_ton_tai_tra_404(client):
    assert client.post("/api/v1/config/apply",
                       json={"kind": "jobs", "id": "khong_co", "applied": True}).status_code == 404


def test_doc_xoa_cau_hinh_khong_ton_tai_tra_404(client):
    params = {"kind": "jobs", "id": "khong_co"}
    assert client.get("/api/v1/config/item", params=params).status_code == 404
    assert client.delete("/api/v1/config/item", params=params).status_code == 404


# ===========================================================================
# 10) XUẤT PDF
# ===========================================================================
def test_xuat_pdf_khi_chua_co_bao_cao_tra_404(client, tmp_path):
    sid = _seed_session(tmp_path, documents={"documents": []})
    assert client.get(f"/api/v1/sessions/{sid}/export.pdf").status_code == 404


# ===========================================================================
# 11) KHÓA TIẾN ĐỘ CỦA BƯỚC KIỂM TRA
# ===========================================================================
@pytest.fixture()
def fake_validate(monkeypatch):
    """Thay chuỗi kiểm tra (gộp tài liệu -> RAG -> LLM -> nhật ký) bằng bản giả: test
    này kiểm ĐÚNG MỘT THỨ — tiến độ được ghi dưới khóa nào."""
    async def _report(*_a, **_k):
        return {"market_name": "", "job_type_name": "", "documents": [], "_meta": {}}

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
    sid = _seed_session(tmp_path, documents={"documents": [], "dossier": {}})
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
    sid = _seed_session(tmp_path, documents={"documents": [], "dossier": {}})
    assert client.post(f"/api/v1/sessions/{sid}/validate", json={"documents": []}).status_code == 200
    assert clean_progress[sid]["stage"] == "done"


# ===========================================================================
# 12) NGÀY KÝ — MỘT đường sửa duy nhất, và nó phải tới được bộ lọc quy định
# ===========================================================================
def _doc_ngay_ky() -> dict:
    return {"documents": [{
        "doc_id": "doc1", "source_file": "hd.pdf",
        "contract": {
            "extracted_fields": {"ngay_ky_hop_dong": {"value": None, "confidence": 0.0}},
            "contract_meta": {"market_id": "dai_loan"},
        },
    }], "dossier": {}}


@pytest.mark.parametrize("nhap,iso", [
    ("2025-03-01", "2025-03-01"),
    ("01/03/2025", "2025-03-01"),
    ("ngày 01 tháng 3 năm 2025", "2025-03-01"),
])
def test_sua_ngay_ky_dong_bo_sang_contract_meta(client, tmp_path, nhap, iso):
    """REGRESSION: bước kiểm tra đọc ngày ký ở `derived`/`contract_meta`, KHÔNG đọc
    trường catalog. Không đồng bộ thì người duyệt sửa ngày ký xong, màn hình hiện ngày
    mới còn bộ lọc hiệu lực văn bản vẫn chạy trên ngày cũ — sai cả bộ căn cứ pháp lý
    mà không có dấu hiệu nào. Giá trị cũng phải NẮN VỀ ISO: '01/03/2025' để nguyên thì
    `date_to_int` gom chữ số ra 1032025, một mốc vô nghĩa."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky())
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"ngay_ky_hop_dong": nhap}})
    assert r.status_code == 200

    contract = read_json(get_paths(sid).documents_json)["documents"][0]["contract"]
    assert contract["contract_meta"]["signed_date"] == iso
    assert contract["derived"]["signed_date"]["value"] == iso
    # Trường catalog cũng phải là ISO: `signed_date_of` đọc nó TRƯỚC, nên để nguyên
    # chuỗi người dùng gõ thì chỗ nắn ISO ở trên bị vô hiệu.
    assert contract["extracted_fields"]["ngay_ky_hop_dong"]["value"] == iso
    assert signed_date_of(contract) == iso


def test_xoa_ngay_ky_thi_go_luon_o_contract_meta(client, tmp_path):
    """Xóa trắng ngày ký phải xóa ở CẢ hai chỗ, nếu không bộ lọc vẫn ôm ngày cũ."""
    sid = _seed_session(tmp_path, documents=_doc_ngay_ky())
    client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                 json={"fields": {"ngay_ky_hop_dong": "2025-03-01"}})
    client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                 json={"fields": {"ngay_ky_hop_dong": None}})
    contract = read_json(get_paths(sid).documents_json)["documents"][0]["contract"]
    assert contract["contract_meta"]["signed_date"] == ""
    assert contract["derived"]["signed_date"]["value"] is None


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
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"ngay_ky_hop_dong": "hôm nọ"}})
    assert r.status_code == 400
    contract = read_json(get_paths(sid).documents_json)["documents"][0]["contract"]
    assert contract["extracted_fields"]["ngay_ky_hop_dong"]["value"] is None


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
# 10) ĐỆM: khóa phiên và báo cáo chỉ được trượt khi có thứ THẬT SỰ đổi
# ===========================================================================
def test_khoa_dem_phien_khong_doi_khi_dao_thu_tu_file_va_doi_nhan_hien_thi():
    """REGRESSION: khóa đệm trượt là OCR lại từ đầu rồi chạy lại LLM cho ĐÚNG bộ hồ sơ
    vừa kiểm xong — 10-30 phút cho một thay đổi không tồn tại.

    Bản cũ nối nội dung file theo THỨ TỰ TẢI LÊN và đưa cả tên hiển thị của thị trường
    vào khóa, nên chọn lại cùng bộ file theo thứ tự khác, hoặc sửa một nhãn trong
    markets.json, là mọi khóa cũ chết."""
    from app.domain.documents.intake import Selection, cache_key_of

    sel = Selection(job_id="nhat_ban", job_prompt={}, market="nhat_ban",
                    market_name="Japan (Nhật Bản)", job_type="tts",
                    job_type_name="Technical intern trainee")
    sel_doi_nhan = Selection(job_id="nhat_ban", job_prompt={}, market="nhat_ban",
                             market_name="Nhật Bản", job_type="tts",
                             job_type_name="TTS")
    a, b = b"%PDF-A", b"%PDF-B"

    assert cache_key_of([a, b], sel) == cache_key_of([b, a], sel)
    assert cache_key_of([a, b], sel) == cache_key_of([a, b], sel_doi_nhan)
    # Vẫn phải đổi khi NỘI DUNG hoặc LỰA CHỌN thật sự đổi.
    assert cache_key_of([a, b], sel) != cache_key_of([a, b"%PDF-C"], sel)
    sel_khac = Selection(job_id="nhat_ban", job_prompt={}, market="nhat_ban",
                         market_name="Japan (Nhật Bản)", job_type="ky_nang_dac_dinh",
                         job_type_name="SSW")
    assert cache_key_of([a, b], sel) != cache_key_of([a, b], sel_khac)


def test_sua_truong_ve_dung_gia_tri_cu_thi_KHONG_xoa_bao_cao(client, tmp_path):
    """REGRESSION: mọi lần PATCH đều xóa `final_report.json`, kể cả khi không có gì đổi.

    Mở ô nhập rồi đóng lại y nguyên là mất báo cáo, và lượt kiểm tra sau phải chạy lại
    LLM từ đầu — 7-20 phút cho một thay đổi không tồn tại."""
    docs = {"documents": [{
        "doc_id": "doc1", "source_file": "hd.pdf",
        "contract": {
            "extracted_fields": {"ngay_ky_hop_dong": {"value": "2025-03-01", "confidence": 1.0}},
            "contract_meta": {"market_id": "dai_loan", "signed_date": "2025-03-01"},
        },
    }], "dossier": {}}
    sid = _seed_session(tmp_path, documents=docs, report={"_meta": {"req_sig": "x"}})
    paths = get_paths(sid)

    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     # cùng ngày, khác cách viết
                     json={"fields": {"ngay_ky_hop_dong": "01/03/2025"}})
    assert r.status_code == 200
    assert r.json()["updated"] == [] and r.json()["unchanged"] == ["ngay_ky_hop_dong"]
    assert read_json(paths.final_report_json)["_meta"]["req_sig"] == "x"

    # Đổi THẬT thì vẫn phải xóa báo cáo cũ.
    r = client.patch(f"/api/v1/sessions/{sid}/documents/doc1/fields",
                     json={"fields": {"ngay_ky_hop_dong": "02/03/2025"}})
    assert r.json()["updated"] == ["ngay_ky_hop_dong"]
    assert client.get(f"/api/v1/sessions/{sid}/report").status_code == 404
