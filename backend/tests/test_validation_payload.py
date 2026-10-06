"""Test PAYLOAD KIỂM TRA — thứ tự khối (tái dùng KV-cache) + bộ trường hỏi LLM.

Hai thứ này không lộ ra ở đầu ra nghiệp vụ: payload sai thứ tự vẫn cho kết luận
đúng, chỉ là chậm; hỏi LLM thừa trường cũng vẫn ra kết luận đúng, chỉ là tốn token.
Không có test thì lần sửa sau vô tình đảo lại cũng không ai biết.
"""
import asyncio

from app.domain.compliance.payload import build_validation_payload
from app.domain.compliance.validation import validate_contract


def _job_prompt():
    return {
        "job_id": "nhat_ban", "display_name": "Nhật Bản", "jurisdiction": "VN",
        "field_check_mode": {"always_check": []},
        "fields_catalog": {
            "tien_luong": {"label": "Tiền lương", "field_group": "check",
                           "check_type": "regulated", "field_section": "Lương & khấu trừ"},
            "tien_dich_vu": {"label": "Tiền dịch vụ", "field_group": "payer",
                             "check_type": "regulated", "field_section": "Người lao động phải trả"},
            "ten_doanh_nghiep": {"label": "Tên doanh nghiệp", "field_group": "declaration",
                                 "check_type": "declaration", "field_section": "Hồ sơ & công văn"},
        },
    }


def _contract():
    return {
        "contract_meta": {"market_name": "Nhật Bản", "job_type_name": ""},
        "extracted_fields": {
            "tien_luong": {"label": "Tiền lương", "value": {"amount": 180000, "currency": "JPY"}},
            "tien_dich_vu": {"label": "Tiền dịch vụ", "value": {"amount": 0, "currency": "VND"}},
            "ten_doanh_nghiep": {"label": "Tên doanh nghiệp", "value": "Công ty A"},
        },
    }


def test_payload_xep_bat_bien_truoc_ho_so_sau():
    """Thứ tự khóa = thứ tự token. Phần BẤT BIẾN phải nằm TRƯỚC phần đổi theo hồ sơ,
    nếu không thì mỗi hồ sơ đều phải prefill lại toàn bộ payload (đắt nhất trên CPU)."""
    _, payload = build_validation_payload(
        _job_prompt(), _contract(), ["tien_luong"],
        [{"id": "c1", "text": "Điều 1...", "metadata": {"source_doc": "Luật"}}],
    )
    keys = list(payload)
    # Luật chơi -> đoạn luật của thị trường -> giá trị hồ sơ.
    assert keys.index("task") < keys.index("regulations") < keys.index("contract_json")
    assert keys.index("output_schema") < keys.index("regulations")
    assert keys[-1] == "contract_json", "giá trị hồ sơ phải là khối CUỐI CÙNG"


def test_doan_luat_sap_on_dinh_theo_chunk_id():
    """Cùng bộ đoạn luật, thứ tự rerank khác nhau vẫn phải ra CÙNG một chuỗi token —
    không thì KV-cache lệch ngay từ đoạn đầu."""
    chunks = [{"id": "b", "text": "B", "metadata": {}}, {"id": "a", "text": "A", "metadata": {}}]
    _, p1 = build_validation_payload(_job_prompt(), _contract(), ["tien_luong"], chunks)
    _, p2 = build_validation_payload(_job_prompt(), _contract(), ["tien_luong"], chunks[::-1])
    assert p1["regulations"] == p2["regulations"]
    assert [c["chunk_id"] for c in p1["regulations"]] == ["a", "b"]


def test_khong_hoi_llm_truong_da_co_ket_luan_tat_dinh(monkeypatch):
    """Nhóm chi phí (`payer`) đã được `check_payer_cost` kết luận tất định — hỏi LLM
    về chúng là trả tiền token cho câu trả lời sẽ bị vứt đi ở reconcile."""
    seen: dict = {}

    async def _fake_run_validation(job_prompt, contract, fields_to_check, rag_chunks):
        seen["fields"] = list(fields_to_check)
        return {"checks": [], "overall_verdict": "PASS"}

    monkeypatch.setattr("app.domain.compliance.validation.run_validation", _fake_run_validation)
    monkeypatch.setattr("app.domain.compliance.validation.query_regulations_for_fields",
                        lambda **_kw: [])

    asyncio.run(validate_contract(_contract(), _job_prompt(), ["tien_luong", "tien_dich_vu"], ""))
    assert seen["fields"] == ["tien_luong"]
    assert "tien_dich_vu" not in seen["fields"]      # chi phí: tất định
    assert "ten_doanh_nghiep" not in seen["fields"]  # khai báo: không có ngưỡng để đối chiếu


def test_schema_khong_hoi_lai_thu_backend_se_ghi_de():
    """`reconcile_checks` ghi đè `title` và `field_value` bằng nhãn catalog và giá trị đã
    trích. Hỏi model sinh ra chúng là trả tiền token cho thứ bị vứt — và với trường điều
    khoản dài (200-300 ký tự mỗi trường) mỗi chuỗi dài là một chỗ model có thể lặp."""
    from app.domain.compliance.payload import VALIDATION_SCHEMA

    khoa = set(VALIDATION_SCHEMA["properties"]["checks"]["items"]["properties"])
    assert "title" not in khoa and "field_value" not in khoa, khoa
    # Những khóa CÒN LẠI vẫn phải đủ để dựng một kết luận đọc được.
    assert {"check_id", "verdict", "reason", "citations"} <= khoa
