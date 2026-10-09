"""Test PAYLOAD KIỂM TRA — thứ tự khối (tái dùng KV-cache) + bộ trường hỏi LLM + truy vấn RAG.

Mấy thứ này không lộ ra ở đầu ra nghiệp vụ: payload sai thứ tự vẫn cho kết luận
đúng, chỉ là chậm; hỏi LLM thừa trường cũng vẫn ra kết luận đúng, chỉ là tốn token.
Không có test thì lần sửa sau vô tình đảo lại cũng không ai biết.
"""
import asyncio
import json

import pytest

from app.core import settings
from app.domain.compliance import validation
from app.domain.compliance.payload import VALIDATION_SCHEMA, build_validation_payload
from app.domain.compliance.validation import (
    prefetch_field_set_regulations,
    regulation_queries,
    validate_contract,
)
from app.store import load_field_set


def _job_prompt():
    return {
        "id": "dich_vu_mau", "display_name": "Hợp đồng dịch vụ mẫu",
        "document_kind": "hợp đồng dịch vụ", "jurisdiction": "VN", "doc_types": ["regulation"],
        "field_check_mode": {"always_check": ["gia_tri"]},
        "fields_catalog": {
            "gia_tri": {"label": "Giá trị hợp đồng", "check_type": "regulated",
                        "check_aspect": "Ghi rõ số tiền và đơn vị tiền tệ.",
                        "fill_hint": "vd '120.000.000 VND'"},
            "thoi_han": {"label": "Thời hạn", "check_type": "regulated",
                         "check_aspect": "Thời hạn phải xác định rõ."},
            "so_luong": {"label": "Số lượng", "check_type": "positive_integer"},
            "ben_a": {"label": "Bên A", "check_type": "declaration"},
        },
    }


def _contract():
    return {
        "contract_meta": {"field_set_id": "dich_vu_mau"},
        "extracted_fields": {
            "gia_tri": {"label": "Giá trị hợp đồng",
                        "value": {"amount": 120_000_000, "currency": "VND"},
                        "evidence": {"short_quote": "Tổng giá trị: 120.000.000 VND"},
                        "confidence": 0.9},
            "thoi_han": {"label": "Thời hạn", "value": "12 tháng"},
            "so_luong": {"label": "Số lượng", "value": 3},
            "ben_a": {"label": "Bên A", "value": "Công ty A"},
        },
    }


_CHUNK = {"id": "c1", "text": "Điều 2. Giá trị hợp đồng...",
          "metadata": {"source_doc": "Quy định mẫu", "doc_type": "regulation",
                       "jurisdiction": "VN", "effective_from": "2020-01-01",
                       "effective_to": "9999-12-31", "content_sha256": "khong-gui"}}


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------
def test_payload_xep_bat_bien_truoc_ho_so_sau():
    """Thứ tự khóa = thứ tự token. Phần BẤT BIẾN phải nằm TRƯỚC phần đổi theo hồ sơ,
    nếu không thì mỗi hồ sơ đều phải prefill lại toàn bộ payload (đắt nhất trên CPU)."""
    system, payload = build_validation_payload(_job_prompt(), _contract(), ["gia_tri"], [_CHUNK])
    assert system, "phải có lời dặn hệ thống"
    keys = list(payload)
    assert keys.index("task") < keys.index("regulations") < keys.index("contract_json")
    assert keys.index("output_schema") < keys.index("job_prompt") < keys.index("regulations")
    assert keys[-1] == "contract_json", "giá trị hồ sơ phải là khối CUỐI CÙNG"


def test_payload_chi_gui_dung_truong_can_kiem_va_gon():
    _, p = build_validation_payload(_job_prompt(), _contract(), ["gia_tri"], [_CHUNK])
    jp = p["job_prompt"]
    assert jp["field_set_id"] == "dich_vu_mau" and jp["document_kind"] == "hợp đồng dịch vụ"
    # Catalog: đúng trường cần kiểm, chỉ nhãn + tiêu chí (bỏ hướng dẫn nhập liệu).
    assert jp["fields_catalog"] == {"gia_tri": {"label": "Giá trị hợp đồng",
                                                "check_aspect": "Ghi rõ số tiền và đơn vị tiền tệ."}}
    # Hồ sơ: chỉ {label, value} — không evidence/confidence.
    assert p["contract_json"]["extracted_fields"] == {
        "gia_tri": {"label": "Giá trị hợp đồng",
                    "value": {"amount": 120_000_000, "currency": "VND"}}}
    assert p["fields_to_check"] == ["gia_tri"]
    # Đoạn quy định: metadata gọn, không mang khóa quản trị.
    meta = p["regulations"][0]["metadata"]
    assert set(meta) == {"source_doc", "jurisdiction", "doc_type", "effective_from", "effective_to"}


def test_doan_quy_dinh_bi_cat_theo_rag_chunk_chars(monkeypatch):
    monkeypatch.setattr(settings, "rag_chunk_chars", 10)
    _, p = build_validation_payload(_job_prompt(), _contract(), ["gia_tri"],
                                    [{**_CHUNK, "text": "X" * 50}])
    assert p["regulations"][0]["text"] == "X" * 10


def test_payload_khong_phinh_theo_so_truong_khong_kiem():
    """Kích thước payload chỉ phụ thuộc trường CẦN kiểm: thêm trường khai báo dài vào
    hồ sơ không được làm payload lớn lên."""
    _, p1 = build_validation_payload(_job_prompt(), _contract(), ["gia_tri"], [_CHUNK])
    c2 = _contract()
    c2["extracted_fields"]["ben_a"]["value"] = "Công ty A " * 500
    _, p2 = build_validation_payload(_job_prompt(), c2, ["gia_tri"], [_CHUNK])
    assert len(json.dumps(p1, ensure_ascii=False)) == len(json.dumps(p2, ensure_ascii=False))


def test_doan_quy_dinh_sap_on_dinh_theo_chunk_id():
    """Cùng bộ đoạn, thứ tự rerank khác nhau vẫn phải ra CÙNG một chuỗi token —
    không thì KV-cache lệch ngay từ đoạn đầu."""
    chunks = [{"id": "b", "text": "B", "metadata": {}}, {"id": "a", "text": "A", "metadata": {}}]
    _, p1 = build_validation_payload(_job_prompt(), _contract(), ["gia_tri"], chunks)
    _, p2 = build_validation_payload(_job_prompt(), _contract(), ["gia_tri"], chunks[::-1])
    assert p1["regulations"] == p2["regulations"]
    assert [c["chunk_id"] for c in p1["regulations"]] == ["a", "b"]


def test_schema_khong_hoi_lai_thu_backend_se_ghi_de():
    """`reconcile_checks` ghi đè `title` và `field_value` bằng nhãn catalog và giá trị đã
    trích. Hỏi model sinh ra chúng là trả tiền token cho thứ bị vứt."""
    khoa = set(VALIDATION_SCHEMA["properties"]["checks"]["items"]["properties"])
    assert "title" not in khoa and "field_value" not in khoa, khoa
    assert {"check_id", "verdict", "reason", "citations"} <= khoa
    verdicts = VALIDATION_SCHEMA["properties"]["overall_verdict"]["enum"]
    assert verdicts == ["PASS", "FAIL", "NEEDS_SUPPLEMENT"]


# ---------------------------------------------------------------------------
# Câu truy vấn quy định
# ---------------------------------------------------------------------------
def test_cau_truy_van_ghep_loai_ho_so_nhan_va_tieu_chi():
    """Truy vấn KHÔNG dùng giá trị OCR — nhờ vậy chạy được ngay khi chọn bộ trường."""
    fc = _job_prompt()["fields_catalog"]
    qs, keys = regulation_queries(fc, ["gia_tri", "thoi_han", "khong_co"], "hợp đồng dịch vụ")
    assert keys == ["gia_tri", "thoi_han", "khong_co"]
    assert qs[0] == "hợp đồng dịch vụ Giá trị hợp đồng Ghi rõ số tiền và đơn vị tiền tệ."
    assert qs[1] == "hợp đồng dịch vụ Thời hạn Thời hạn phải xác định rõ."
    assert qs[2] == "hợp đồng dịch vụ khong_co khong_co", "trường lạ: dùng chính mã trường"
    # Không có trường nào -> một truy vấn chung theo loại hồ sơ, không nhãn trường.
    assert regulation_queries(fc, [], "hợp đồng") == (["hợp đồng"], [""])
    assert regulation_queries(fc, [], "") == (["quy định"], [""])


def test_nap_truoc_dung_sieu_tap_truong_regulated(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(validation, "prefetch_regulations", lambda **kw: seen.update(kw))
    prefetch_field_set_regulations(_job_prompt())
    assert len(seen["field_queries"]) == 2       # gia_tri + thoi_han, không khai báo/tất định
    assert all(q.startswith("hợp đồng dịch vụ ") for q in seen["field_queries"])
    assert seen["jurisdiction"] == "VN" and seen["doc_types"] == ["regulation"]
    assert seen["per_field_k"] == validation._PER_FIELD_K


def test_bo_truong_mau_ra_truy_van_theo_document_kind(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(validation, "prefetch_regulations", lambda **kw: seen.update(kw))
    fs = load_field_set("hop_dong_mau")
    prefetch_field_set_regulations(fs)
    regulated = [k for k, e in fs["fields_catalog"].items()
                 if e.get("check_type", "regulated") == "regulated"]
    assert len(seen["field_queries"]) == len(regulated)
    assert all(q.startswith(fs["document_kind"] + " ") for q in seen["field_queries"])


# ---------------------------------------------------------------------------
# validate_contract (mô hình + RAG giả)
# ---------------------------------------------------------------------------
@pytest.fixture()
def gia_lap(monkeypatch):
    seen: dict = {"progress": []}

    async def _fake_run_validation(job_prompt, contract, fields_to_check, rag_chunks):
        seen["fields"] = list(fields_to_check)
        return {"checks": [
            {"check_id": k, "verdict": "PASS", "reason": "ok", "citations": [{"chunk_id": "c1"}]}
            for k in fields_to_check
        ]}

    def _fake_rag(**kw):
        seen["rag"] = kw
        return [{**_CHUNK, "field_ranks": dict.fromkeys(kw["field_keys"], 0)}]

    monkeypatch.setattr(validation, "run_validation", _fake_run_validation)
    monkeypatch.setattr(validation, "query_regulations_for_fields", _fake_rag)
    return seen


def test_chi_hoi_llm_truong_regulated_co_gia_tri(gia_lap):
    """Trường khai báo không có ngưỡng để đối chiếu; trường tất định đã có kết luận bằng
    mã — hỏi LLM về chúng là trả tiền token cho câu trả lời sẽ bị vứt."""
    res = asyncio.run(validate_contract(
        _contract(), _job_prompt(), ["thoi_han", "so_luong"], "2025-01-01",
        on_progress=gia_lap["progress"].append))
    assert gia_lap["fields"] == ["gia_tri", "thoi_han"]
    assert gia_lap["rag"]["signed_date"] == "2025-01-01"
    assert gia_lap["rag"]["field_keys"] == ["gia_tri", "thoi_han"]
    assert gia_lap["rag"]["doc_types"] == ["regulation"]
    assert gia_lap["progress"] == ["rag", "llm:2", "reconcile"]

    by = {c["check_id"]: c for c in res["checks"]}
    assert by["ben_a"]["verdict"] == "DECLARATION"
    assert by["so_luong"]["verdict"] == "PASS" and by["so_luong"]["deterministic"]
    assert by["gia_tri"]["citations"][0]["source_doc"] == "Quy định mẫu"
    assert res["overall_verdict"] == "PASS"
    assert res["_metrics"]["retrieval"] and res["_metrics"]["citation"]


def test_truong_khong_chon_khong_bat_buoc_thi_khong_kiem(gia_lap):
    res = asyncio.run(validate_contract(_contract(), _job_prompt(), [], ""))
    assert gia_lap["fields"] == ["gia_tri"]        # chỉ trường bắt buộc
    assert "thoi_han" not in {c["check_id"] for c in res["checks"]}
    assert gia_lap["rag"]["signed_date"] == "", "không có ngày ký -> không lọc hiệu lực"


def test_truong_duoc_chon_nhung_trong_khong_bien_mat(gia_lap):
    c = _contract()
    c["extracted_fields"]["thoi_han"]["value"] = ""
    res = asyncio.run(validate_contract(c, _job_prompt(), ["thoi_han"], ""))
    th = next(x for x in res["checks"] if x["check_id"] == "thoi_han")
    assert th["verdict"] == "NEEDS_SUPPLEMENT" and th["missing_fields"] == ["thoi_han"]
    assert res["overall_verdict"] == "NEEDS_SUPPLEMENT"


def test_khong_co_truong_regulated_thi_khong_ket_luan_dat(gia_lap):
    """Bỏ qua bước LLM phải LỘ RA: cờ cảnh báo + kết luận chung không được là PASS."""
    jp = _job_prompt()
    jp["field_check_mode"]["always_check"] = []
    c = _contract()
    res = asyncio.run(validate_contract(c, jp, [], ""))
    assert "fields" not in gia_lap, "không có trường nào thì không gọi mô hình"
    assert any(f["code"] == "NO_REGULATED_FIELDS" for f in c["input_flags"])
    assert res["overall_verdict"] == "NEEDS_SUPPLEMENT"
