"""NGHIỆP VỤ KIỂM TRA (payload) — dựng payload gọn + VALIDATION_SCHEMA (structured outputs) + gọi LLM đối chiếu.

VALIDATION_SCHEMA: JSON Schema cho Ollama structured outputs — ép model trả đúng
{overall_verdict, checks:[...]}, loại gần hết lỗi JSON sai định dạng của model nhỏ.
"""
from __future__ import annotations

import json
from typing import Any

from app import metrics
from app.core import settings
from app.llm import call_llm_json, fit_num_ctx
from app.store import load_validation_prompt, slim_fields_catalog

# TRÍCH DẪN: model CHỈ trả `chunk_id`. Tên văn bản, khoảng hiệu lực và nguyên văn điều
# khoản đều do backend điền lại từ chính đoạn đã gửi đi (xem `reconcile._chunk_to_citation`).
#
# Vì sao bỏ `text_quote` khỏi đầu ra: nó bắt model CHÉP LẠI nguyên văn điều khoản cho
# TỪNG kết luận. Một lượt 15 trường × ít nhất 1 trích dẫn × ~800 ký tự là khoảng 5.000
# token sinh ra chỉ để chép lại thứ backend đã có sẵn — trên CPU là 20-40 phút, tức phần
# lớn thời gian của cả lượt kiểm tra và là nguyên nhân chạm trần `llm_timeout_seconds`.
# Kèm theo: chép tay thì có đường bịa nguyên văn luật, còn điền từ chunk thì không.
_CITATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"chunk_id": {"type": "string"}},
    "required": ["chunk_id"],
}

# Khớp output_schema trong prompts/services/validation_prompt.json.
VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overall_verdict": {"type": "string", "enum": ["PASS", "FAIL", "NEEDS_SUPPLEMENT"]},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_id": {"type": "string"},
                    # KHÔNG hỏi `title` và `field_value`: `reconcile_checks` GHI ĐÈ cả hai
                    # bằng nhãn trong catalog và giá trị trong `extracted_fields` (model
                    # hay nhồi cả trích dẫn + chunk-id vào title làm vỡ cột hiển thị).
                    # Bắt model sinh ra thứ sẽ bị vứt là trả tiền token cho rác — với
                    # trường điều khoản dài, `field_value` là 200-300 ký tự mỗi trường,
                    # và mỗi chuỗi dài là một chỗ model có thể rơi vào vòng lặp.
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "reasoning": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL", "NEEDS_SUPPLEMENT"]},
                    "reason": {"type": "string"},
                    "missing_fields": {"type": "array", "items": {"type": "string"}},
                    "fields_used": {"type": "array", "items": {"type": "string"}},
                    "citations": {"type": "array", "items": _CITATION_SCHEMA},
                },
                "required": ["check_id", "verdict", "reason"],
            },
        },
    },
    "required": ["checks"],
}


def build_validation_payload(
    job_prompt: dict[str, Any],
    contract_json: dict[str, Any],
    fields_to_check: list[str],
    rag_chunks: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    """Trả về (system, user_payload) cho LLM kiểm tra.

    fields_to_check: DANH SÁCH TRƯỜNG cần kiểm tra đã tính sẵn ở backend
    (always_check + optional_check người dùng đã chọn). LLM CHỈ tạo đúng 1 check
    cho mỗi trường trong danh sách này -> không tự suy diễn always/optional, không
    sinh NOT_APPLICABLE, ít token đầu ra hơn (tránh model trả thiếu trường)."""
    prompt = load_validation_prompt()

    # Mỗi đoạn quy định gửi cho LLM: chunk_id + text + metadata GỌN (chỉ các trường
    # dùng cho trích dẫn). LLM PHẢI sao chép nguyên các trường này vào citations.
    def _slim_meta(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_doc": m.get("source_doc"),      # tên văn bản CÓ DẤU để hiển thị
            "jurisdiction": m.get("jurisdiction"),
            "doc_type": m.get("doc_type"),
            "effective_from": m.get("effective_from"),
            "effective_to": m.get("effective_to"),
        }

    # Cắt mỗi đoạn luật còn `rag_chunk_chars` ký tự: đủ ngữ nghĩa đối chiếu mà giảm
    # mạnh prompt token, và prefill trên CPU là phần tốn nhất của một lượt gọi. Trích
    # dẫn HIỂN THỊ lấy từ `rag_chunks` GỐC ở reconcile nên người dùng không mất gì.
    # SẮP THEO chunk_id: cùng một thị trường, hai hồ sơ khác nhau vẫn kéo về gần như
    # cùng bộ đoạn luật nhưng THỨ TỰ theo điểm rerank thì đổi lung tung -> chuỗi token
    # khác nhau ngay từ đoạn đầu -> KV-cache của Ollama không tái dùng được gì.
    _cap = int(getattr(settings, "rag_chunk_chars", 800) or 800)
    regulations_payload = sorted(
        ({"chunk_id": c.get("id"), "text": (c.get("text") or "")[:_cap],
          "metadata": _slim_meta(c.get("metadata", {}))}
         for c in rag_chunks),
        key=lambda c: str(c.get("chunk_id") or ""),
    )

    # Chỉ gửi catalog của ĐÚNG các trường cần kiểm (label + check_aspect) -> gọn,
    # đúng trọng tâm, giảm token. Bỏ fill_hint (hướng dẫn nhập liệu).
    full_slim = slim_fields_catalog(job_prompt.get("fields_catalog", {}))
    fields_catalog_slim = {k: full_slim[k] for k in fields_to_check if k in full_slim}

    job_prompt_slim = {
        "job_id": job_prompt.get("job_id"),
        "display_name": job_prompt.get("display_name"),
        "jurisdiction": job_prompt.get("jurisdiction"),
        "fields_catalog": fields_catalog_slim,
    }

    # Contract GỌN: chỉ gửi các trường CẦN KIỂM, mỗi trường {label, value} — bỏ
    # evidence/confidence/input_flags/derived (backend xử lý tất định, LLM không cần).
    # Gửi cả catalog kèm evidence quote thì payload phình lên nhiều lần mà không thêm
    # thông tin nào LLM dùng được.
    _ef = contract_json.get("extracted_fields", {}) or {}
    contract_for_llm = {
        "contract_meta": contract_json.get("contract_meta", {}) or {},
        "extracted_fields": {
            k: {"label": (_ef.get(k) or {}).get("label"),
                "value": (_ef.get(k) or {}).get("value")}
            for k in fields_to_check if k in _ef
        },
    }

    # THỨ TỰ KHÓA = THỨ TỰ TOKEN (json.dumps giữ nguyên thứ tự chèn), và llama.cpp/
    # Ollama chỉ tái dùng KV-cache cho phần ĐẦU giống hệt nhau giữa hai lần gọi.
    # Xếp từ BẤT BIẾN NHẤT tới THAY ĐỔI NHIỀU NHẤT:
    #   1) luật chơi (prompt_id/task/schema/post_rules)  — không đổi giữa mọi lần gọi;
    #   2) đoạn luật của THỊ TRƯỜNG + catalog trường     — không đổi trong một phiên
    #      duyệt nhiều hồ sơ cùng thị trường;
    #   3) giá trị của HỒ SƠ                             — đổi mỗi lần.
    # Đặt contract_json lên sớm thì hồ sơ nào cũng phải prefill lại TOÀN BỘ payload;
    # trên CPU prefill là phần tốn nhất.
    user_payload = {
        # --- (1) BẤT BIẾN ---
        "prompt_id": prompt.get("prompt_id"),
        "prompt_version": prompt.get("version"),
        "task": prompt.get("task", []),
        "output_schema": prompt.get("output_schema", {}),
        "post_rules": prompt.get("post_rules", []),
        "final_verdict_policy": prompt.get("final_verdict_policy", {}),
        "input_contract_convention": prompt.get("input_contract_convention", {}),
        # --- (2) THEO THỊ TRƯỜNG / BỘ TRƯỜNG ĐANG KIỂM ---
        "job_prompt": job_prompt_slim,
        "fields_to_check": fields_to_check,
        "regulations": regulations_payload,
        # --- (3) THEO HỒ SƠ ---
        "contract_json": contract_for_llm,
    }
    return prompt.get("system", []), user_payload


async def run_validation(
    job_prompt: dict[str, Any],
    contract_json: dict[str, Any],
    fields_to_check: list[str],
    rag_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """MỘT lần gọi LLM duy nhất cho cả lượt kiểm tra: đối chiếu mọi trường với các
    đoạn luật đã truy hồi, trả kết luận + trích dẫn theo JSON Schema cố định.

    Gọi một lần cho tất cả các trường (không phải mỗi trường một lần) vì các trường
    ràng buộc lẫn nhau — lương với thời giờ làm việc, tiền dịch vụ với thời hạn hợp
    đồng — và vì trên máy local mỗi lần gọi thêm là thêm một lượt sinh chữ trên CPU."""
    system, user_payload = build_validation_payload(
        job_prompt, contract_json, fields_to_check, rag_chunks,
    )
    # ĐO payload thật mỗi lần gọi. Cửa sổ thừa KHÔNG miễn phí: Ollama cấp phát KV
    # buffer theo num_ctx × OLLAMA_NUM_PARALLEL, nên `validation_num_ctx` đặt rộng tay
    # là vài GB RAM cho phần không dùng tới -> Ollama đuổi model rồi nạp lại mỗi lượt.
    # `call_llm_json` tự co cửa sổ về vừa payload (`fit_num_ctx`); dòng log in ra CẢ
    # mức cấu hình lẫn mức thật dùng để chênh lệch nhìn thấy được.
    _n = len(json.dumps(user_payload, ensure_ascii=False)) + len("\n".join(system))
    _cfg = settings.validation_num_ctx or settings.ollama_num_ctx
    # Khai `validation_num_ctx` thì dùng ĐÚNG con số đó (xem `call_llm_json`): mọi lượt
    # gọi tới model kiểm tra phải cùng một cửa sổ, nếu không Ollama nạp lại model hoặc
    # cắt cụt prompt — cả hai đều không báo gì.
    _fit = _cfg if settings.validation_num_ctx else fit_num_ctx(_n, _cfg)
    print(f"[validate] payload ~{_n // 3} token ({_n} ký tự) · {len(fields_to_check)} trường "
          f"· {len(rag_chunks)} đoạn luật · num_ctx {_fit}/{_cfg}")
    # GHI LẠI, không chỉ in ra. Dòng log trên trả lời được câu hỏi "lượt này nặng bao
    # nhiêu" nhưng biến mất cùng cửa sổ terminal, nên không trả lời được câu hỏi thật
    # sự cần: hạ `validation_num_ctx` xuống mức nào thì an toàn. Bộ đếm đi vào báo cáo
    # rồi vào nhật ký, nên so được giữa các lượt và giữa các lần chỉnh cấu hình.
    metrics.bump("payload.calls")
    metrics.bump("payload.chars", _n)
    metrics.bump("payload.fields", len(fields_to_check))
    metrics.bump("payload.chunks", len(rag_chunks))
    metrics.bump("payload.num_ctx_used", _fit)
    metrics.bump("payload.num_ctx_config", _cfg)
    return await call_llm_json(
        system, user_payload,
        models=settings.validation_model or None,
        schema=VALIDATION_SCHEMA,
        # Bước KIỂM TRA gửi payload dài nhất (đoạn luật + toàn bộ trường) và là bước
        # người dùng chờ trực tiếp -> cửa sổ lớn hơn + giữ model trong RAM lâu hơn.
        num_ctx=settings.validation_num_ctx or None,
        keep_alive=settings.validation_keep_alive or None,
    )
