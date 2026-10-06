"""CỔNG CHẶN CHẤT LƯỢNG — hợp đồng API · bảo mật · tải đồng thời · đầu cuối · bất biến frontend.

Năm nhóm dưới đây trả lời năm câu hỏi mà bộ test cũ không hỏi:

  1. HỢP ĐỒNG API   — endpoint có còn trả đúng HÌNH DẠNG mà frontend đọc không.
     Đổi tên một khóa trong `metrics` thì backend vẫn xanh, `tsc` vẫn xanh (kiểu chỉ
     là khai báo), và trang quản trị hiện "—" ở mọi ô. Không có lớp nào bắt được.
  2. BẢO MẬT       — các nhánh phải 401/400 vẫn 401/400 sau mỗi lần thêm endpoint.
  3. TẢI ĐỒNG THỜI  — nhiều lượt đọc cùng lúc không làm hỏng nhau.
  4. ĐẦU CUỐI      — cả đường kiểm tra chạy thông: gộp tài liệu -> báo cáo -> nhật ký,
     với LLM/RAG bị thay bằng bản giả (test kiểm LOGIC, không kiểm môi trường).
  5. FRONTEND      — bất biến đọc được từ chính mã nguồn, không cần trình duyệt: bộ
     khóa i18n hai ngôn ngữ phải khớp, mã trạng thái thô không được lọt ra JSX, mọi
     khóa nhóm chi phí phải rơi vào một khái niệm đã biết.

Kiểm thử trình duyệt THẬT (Playwright) chưa nằm ở đây: nó cần thêm phụ thuộc và một
lượt tải trình duyệt, thuộc bảng kiểm phát hành chứ không thuộc `npm run test`.
"""
from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import core
from app.main import app
from app.store import audit as audit_store
from app.store import config as config_store

ADMIN_TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Mọi đường ghi trỏ vào tmp_path — chạy test không được đụng dữ liệu thật."""
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(core.settings, "admin_token", ADMIN_TOKEN)
    monkeypatch.setattr(audit_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    ucfg = tmp_path / "user_config"
    monkeypatch.setattr(config_store, "USER_CONFIG_DIR", ucfg)
    monkeypatch.setattr(config_store, "APPLIED_FILE", ucfg / "applied.json")
    for kind in list(config_store.USER_CONFIG_DIRS):
        monkeypatch.setitem(config_store.USER_CONFIG_DIRS, kind, ucfg / kind)


@pytest.fixture()
def client():
    return TestClient(app, base_url="http://localhost")


def _keys(obj: dict, *path: str) -> set[str]:
    """Bộ khóa tại một đường dẫn lồng nhau ('' nếu đứt giữa chừng)."""
    for p in path:
        obj = obj.get(p) or {}
    return set(obj)


# ===========================================================================
# 1) HỢP ĐỒNG API
# ===========================================================================
def test_openapi_liet_ke_du_endpoint_frontend_dang_goi():
    """Mọi đường dẫn frontend gọi phải có thật trong OpenAPI.

    Đổi tên một endpoint ở backend thì frontend chỉ vỡ LÚC CHẠY, ở đúng trang ít ai
    mở. Đối chiếu thẳng với mã nguồn frontend biến việc đó thành đỏ ngay tại đây."""
    spec_paths = set(app.openapi()["paths"])
    goi = set()
    for p in (FRONTEND_SRC / "api").glob("*.ts"):
        for m in re.finditer(r'"(/api/v1/[^"?$]*)', p.read_text(encoding="utf-8")):
            goi.add(m.group(1).rstrip("/"))
    # Đường dẫn có tham số: `/api/v1/sessions/${id}/...` -> bỏ qua ở phép so tên thẳng.
    thieu = sorted(u for u in goi if u and u not in spec_paths)
    assert not thieu, f"frontend gọi endpoint không có trong OpenAPI: {thieu}"


def test_hop_dong_chi_so_ky_thuat_va_chat_luong(client):
    """Trang quản trị đọc đúng những khóa này; thiếu một khóa là một ô hiện '—'."""
    r = client.get("/api/v1/admin/metrics", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert {"sessions", "total", "window_days", "latency", "quality"} <= set(d)
    assert {"runs", "files", "pages", "bytes", "mb", "ocr_seconds", "check_seconds",
            "seconds_per_page", "seconds_per_file"} <= set(d["total"])
    q = d["quality"]
    assert {"runs", "window_days", "retrieval", "citation", "payload", "corpus"} <= set(q)
    assert {"coverage_avg", "full_coverage_avg", "samples", "uncovered_fields"} <= set(q["retrieval"])
    assert {"precision_avg", "grounded_ratio_avg", "citations", "hallucinated",
            "decided_checks"} <= set(q["citation"])
    assert {"tokens_avg", "fields_avg", "num_ctx_used_avg", "num_ctx_config_avg",
            "num_ctx_headroom_avg"} <= set(q["payload"])
    assert {"current_fingerprint", "stale_runs", "runs_without_fingerprint"} <= set(q["corpus"])


def test_hop_dong_quan_tri_kho_luat(client):
    """Bảng kho luật cần đủ sáu cột; thiếu cột nào là mất đúng khả năng truy vết đó."""
    r = client.get("/api/v1/admin/corpus", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert {"documents", "counts", "blocking", "fingerprint"} <= set(d)
    assert d["documents"], "app/rules phải có ít nhất một văn bản luật"
    assert {"file", "title", "doc_no", "official_source", "corpus_version",
            "effective_from", "effective_to", "sha256", "sha256_actual",
            "approved_by", "approved_at", "status", "status_text"} <= set(d["documents"][0])
    assert len(d["fingerprint"]) == 16


def test_hop_dong_do_tren_ho_so_co_nhan(client):
    """Chưa có nhãn thì phải nói thẳng là chưa có, không trả 0% như một kết quả đo."""
    r = client.get("/api/v1/admin/golden", headers=AUTH)
    assert r.status_code == 200
    s = r.json()["summary"]
    assert {"cases_total", "cases_measured", "extraction_accuracy", "recall_at_k",
            "citation_accuracy"} <= set(s)
    if not s["cases_total"]:
        assert s["extraction_accuracy"] is None and s["recall_at_k"] is None
        assert r.json()["note"]


def test_canh_bao_quy_uoc_ten_thi_truong(client):
    """Tên không theo `"English (Tiếng Việt)"` -> CẢNH BÁO, không phải lỗi chặn."""
    xau = json.dumps({"id": "x", "name": "Thị trường mới", "job_id": "nhat_ban",
                      "job_types": [{"id": "a", "name": "Nghề mới"}]}, ensure_ascii=False)
    w = client.post("/api/v1/config/lint", json={"kind": "markets", "content": xau}).json()
    assert w["ok"] and len(w["warnings"]) == 2, w

    tot = json.dumps({"id": "x", "name": "New market (Thị trường mới)", "job_id": "nhat_ban",
                      "job_types": [{"id": "a", "name": "New job (Nghề mới)"}]}, ensure_ascii=False)
    assert client.post("/api/v1/config/lint", json={"kind": "markets", "content": tot}
                       ).json()["warnings"] == []

    # Đang gõ dở -> JSON hỏng là chuyện thường, không được trả 500.
    hong = client.post("/api/v1/config/lint", json={"kind": "markets", "content": "{"})
    assert hong.status_code == 200 and hong.json()["json_error"] is True

    # Lưu vẫn PHẢI thành công (cảnh báo không chặn), kèm cảnh báo trong phản hồi.
    saved = client.put("/api/v1/config/item", json={"kind": "markets", "id": "x", "content": xau})
    assert saved.status_code == 200 and saved.json()["warnings"]


# ===========================================================================
# 2) BẢO MẬT — hồi quy
# ===========================================================================
@pytest.mark.parametrize("path", ["/api/v1/admin/corpus", "/api/v1/admin/golden"])
def test_endpoint_quan_tri_moi_van_doi_ma(client, path):
    """Thêm endpoint dưới /admin mà quên dependency là mở toang trang quản trị."""
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer sai"}).status_code == 401


def test_phe_duyet_phai_khai_ten_nguoi_duyet(client):
    """Phê duyệt vô danh thì cột 'người phê duyệt' chỉ là trang trí."""
    r = client.post("/api/v1/admin/corpus/approve",
                    json={"file": "Luat_so_69-2020.md", "approved_by": "  "}, headers=AUTH)
    assert r.status_code == 400


def test_phe_duyet_khong_cho_ghi_file_ngoai_thu_muc_luat(client):
    """Tên file đi thẳng vào đường dẫn — phải chặn vượt thư mục."""
    r = client.post("/api/v1/admin/corpus/approve",
                    json={"file": "../../core.py", "approved_by": "Nam"}, headers=AUTH)
    assert r.status_code in (400, 404)


def test_cau_hinh_mac_dinh_da_siet(tmp_path, monkeypatch):
    """Mặc định KHÔNG còn mở: CORS chỉ loopback, mã quản trị tự sinh khi .env bỏ trống."""
    from app import core
    from app.core import Settings
    mac_dinh = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "*" not in mac_dinh.cors_allow_origins
    assert "localhost" in mac_dinh.allowed_hosts

    monkeypatch.setattr(core, "ADMIN_TOKEN_FILE", tmp_path / ".admin_token")
    token = core.ensure_admin_token(mac_dinh)
    assert len(token) >= 24 and mac_dinh.admin_token == token
    assert (tmp_path / ".admin_token").read_text(encoding="utf-8") == token
    lan_sau = Settings(_env_file=None)  # type: ignore[call-arg]
    assert core.ensure_admin_token(lan_sau) == token          # đọc lại, không sinh mã mới


# ===========================================================================
# 3) TẢI ĐỒNG THỜI
# ===========================================================================
def test_nhieu_luot_doc_dong_thoi_khong_hong_nhau(client, tmp_path):
    """20 lượt đọc song song trên 4 endpoint: tất cả phải 200 và trả cùng một kết quả.

    Không phải đo tốc độ — đo TÍNH ĐÚNG dưới đồng thời. Nhật ký là file phẳng đọc lại
    mỗi lần; một lượt ghi chen giữa hai lượt đọc mà làm đứt dòng JSON thì đường đọc
    phải bỏ qua dòng hỏng chứ không được ném 500."""
    audit_store.AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    audit_store.AUDIT_FILE.write_text(
        "".join(json.dumps({"ts": "2026-08-01T00:00:00+07:00", "session_id": str(i),
                            "documents": []}, ensure_ascii=False) + "\n" for i in range(50)),
        encoding="utf-8")

    paths = ["/api/v1/stats", "/api/v1/audit?limit=10", "/api/v1/reminders", "/health"]

    def _hit(i: int) -> tuple[int, str]:
        r = client.get(paths[i % len(paths)])
        return r.status_code, r.text

    with ThreadPoolExecutor(max_workers=8) as pool:
        ket_qua = list(pool.map(_hit, range(20)))
    assert all(code == 200 for code, _ in ket_qua)
    # Cùng một endpoint phải cho cùng một phản hồi — dữ liệu không đổi giữa chừng.
    for i, path in enumerate(paths):
        cua_path = {body for j, (_c, body) in enumerate(ket_qua) if j % len(paths) == i}
        assert len(cua_path) == 1, f"{path} trả kết quả khác nhau giữa các lượt song song"


# ===========================================================================
# 4) ĐẦU CUỐI (LLM/RAG giả) — gộp tài liệu -> báo cáo -> nhật ký
# ===========================================================================
def test_dau_cuoi_tu_ho_so_den_nhat_ky(client, tmp_path, monkeypatch):
    """Cả đường kiểm tra chạy thông và để lại đúng dấu vết cần cho báo cáo.

    Thay `validate_contract` bằng bản giả: bước gọi mô hình là thứ duy nhất cần môi
    trường, mọi bước còn lại (gộp tài liệu, dựng số đo, ghi nhật ký, vân tay kho luật)
    đều là logic thuần và phải kiểm được ở đây."""
    from app.domain.compliance import report as report_mod

    sid = str(uuid.uuid4())
    base = tmp_path / "temp" / sid
    base.mkdir(parents=True)
    (base / "documents.json").write_text(json.dumps({"documents": [{
        "doc_id": "doc1", "source_file": "1. Van ban dang ky hop dong.pdf", "size_bytes": 1000,
        "ocr": {"stats": {"num_pages": 3, "ocr_seconds": 12.5}, "full_text": "van ban dang ky"},
        "contract": {
            "contract_meta": {"session_id": sid, "job_id": "nhat_ban", "market_id": "nhat_ban",
                              "country_id": "nhat_ban", "job_type_id": "tts",
                              "market_name": "Japan (Nhật Bản)", "job_type_name": "TTS"},
            "extracted_fields": {"tien_luong": {"label": "Tiền lương", "value": "184461 JPY"}},
            "input_flags": [],
        },
    }]}, ensure_ascii=False), encoding="utf-8")

    async def _fake_validate(contract, job_prompt, fields, signed_date, on_progress=None):
        return {
            "overall_verdict": "PASS",
            "checks": [{"check_id": "tien_luong", "verdict": "PASS", "reason": "ok",
                        "citations": [{"chunk_id": "c1", "source_doc": "Luật số 69/2020/QH14"}]}],
            "fee_anomalies": [],
            "_metrics": {"retrieval": {"fields": 1, "chunks": 1, "coverage_at_k": 1.0,
                                       "full_coverage_at_k": 1.0, "uncovered_fields": [],
                                       "detail": {"tien_luong": ["c1"]}},
                         "citation": {"citations": 1, "precision": 1.0, "hallucinated": 0,
                                      "decided_checks": 1, "grounded_ratio": 1.0}},
        }

    monkeypatch.setattr(report_mod, "validate_contract", _fake_validate)

    r = client.post(f"/api/v1/sessions/{sid}/validate", json={"selected_fields": ["tien_luong"]})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["overall_verdict"] == "PASS"
    # Vân tay kho luật phải đi cùng báo cáo — không có nó thì báo cáo cũ không đối
    # chiếu được với kho luật hiện tại.
    assert len(rep["_meta"]["corpus_fingerprint"]) == 16
    assert rep["_meta"]["corpus_fingerprint"] in rep["_meta"]["req_sig"]
    assert "payload" in rep["metrics"]

    # Lượt hai cùng chữ ký -> trả lại bản đã lưu, KHÔNG chạy lại.
    assert client.post(f"/api/v1/sessions/{sid}/validate",
                       json={"selected_fields": ["tien_luong"]}).json()["checked_at"] == rep["checked_at"]

    # Nhật ký giữ đủ thứ trang chỉ số cần: số trang, giây OCR, vân tay, số đo.
    ghi = [json.loads(x) for x in audit_store.AUDIT_FILE.read_text(encoding="utf-8").splitlines()]
    assert len(ghi) == 1
    assert ghi[0]["total_pages"] == 3 and ghi[0]["ocr_seconds"] == 12.5
    assert ghi[0]["corpus_fingerprint"] == rep["_meta"]["corpus_fingerprint"]
    assert ghi[0]["metrics"]["retrieval"]["coverage_at_k"] == 1.0

    # ... và trang chỉ số đọc lại được chính số đó.
    q = client.get("/api/v1/admin/metrics", headers=AUTH).json()["quality"]
    assert q["runs"] == 1 and q["retrieval"]["coverage_avg"] == 1.0
    assert q["citation"]["precision_avg"] == 1.0 and q["corpus"]["stale_runs"] == 0


# ===========================================================================
# 5) FRONTEND — bất biến đọc thẳng từ mã nguồn
# ===========================================================================
def _i18n_keys(block: str) -> set[str]:
    return set(re.findall(r'^\s*"([^"]+)":', block, re.M))


def test_hai_ban_ngon_ngu_co_cung_bo_khoa():
    """Khóa thiếu bản English LẶNG LẼ lùi về tiếng Việt — giao diện không vỡ, chỉ lẫn
    một dòng tiếng Việt giữa bảng, nên không ai phát hiện."""
    src = (FRONTEND_SRC / "i18n.ts").read_text(encoding="utf-8")
    vi_start = src.index("const VI: Record<string, string> = {")
    en_start = src.index("const EN: Record<string, string> = {")
    vi = _i18n_keys(src[vi_start:en_start])
    en = _i18n_keys(src[en_start:src.index("\n};", en_start)])
    assert vi and en
    assert not (vi - en), f"khóa thiếu bản English: {sorted(vi - en)[:10]}"
    assert not (en - vi), f"khóa thiếu bản tiếng Việt: {sorted(en - vi)[:10]}"


def test_ma_trang_thai_tho_khong_lot_ra_giao_dien():
    """`NEEDS_SUPPLEMENT` hiện thẳng trên màn hình là một chuỗi mã nguồn, không phải
    câu tiếng Việt. Mã trạng thái chỉ được xuất hiện trong so sánh hoặc bảng tra."""
    xau: list[str] = []
    for p in sorted(FRONTEND_SRC.rglob("*.tsx")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            # Chỉ soi phần VĂN BẢN giữa hai thẻ JSX: >...NEEDS_SUPPLEMENT...<
            if re.search(r">[^<>{]*\b(PASS|FAIL|NEEDS_SUPPLEMENT|DECLARATION)\b[^<>{]*<", line):
                xau.append(f"{p.name}:{i}")
    assert not xau, f"mã trạng thái thô lọt ra JSX: {xau}"


def test_moi_khoa_chi_phi_roi_vao_mot_khai_niem_da_biet():
    """`costConcept` trả 'khac' cho khóa lạ — hai khoản khác nhau cùng rơi vào 'khac'
    sẽ bị ghép cặp với nhau và bảng chi phí so sai hai bên."""
    from app.store import load_job_prompt

    src = (FRONTEND_SRC / "costPairs.ts").read_text(encoding="utf-8")
    mau = re.findall(r"if \(/([^/]+)/\.test\(k\)\) return \"([a-z_]+)\";", src)
    assert mau, "không đọc được bảng khái niệm trong costPairs.ts"

    la: list[str] = []
    fc = load_job_prompt("nhat_ban")["fields_catalog"]
    for k, v in fc.items():
        if (v or {}).get("field_group") != "payer":
            continue
        if not any(re.search(pat, k) for pat, _ in mau):
            la.append(k)
    assert not la, f"khóa nhóm chi phí không khớp khái niệm nào: {la}"
