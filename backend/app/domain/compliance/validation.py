"""NGHIỆP VỤ KIỂM TRA (validation) — orchestrator validate_contract: truy hồi quy định -> mô hình đối chiếu -> reconcile.

Module con trong nhánh kiểm tra:
  payload.py    — dựng payload + VALIDATION_SCHEMA + gọi mô hình (run_validation)
  reconcile.py  — reconcile_checks / merge_contracts / verdict
  validation.py — validate_contract (RAG -> mô hình -> reconcile) + re-export API con
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app import metrics
from app.core import settings
from app.domain.compliance.payload import (  # noqa: F401 — re-export
    VALIDATION_SCHEMA,
    build_validation_payload,
    run_validation,
)
from app.domain.compliance.reconcile import (  # noqa: F401 — re-export
    completeness,
    field_check_type,
    field_group,
    has_value,
    merge_contracts,
    overall_verdict,
    reconcile_checks,
)
from app.domain.regulations import (
    prefetch_regulations,
    query_regulations_for_fields,
)
from app.store import field_check_aspect, field_label

__all__ = [
    "VALIDATION_SCHEMA",
    "build_validation_payload",
    "completeness",
    "field_check_type",
    "field_group",
    "has_value",
    "merge_contracts",
    "overall_verdict",
    "prefetch_field_set_regulations",
    "reconcile_checks",
    "regulation_queries",
    "run_validation",
    "validate_contract",
]


# Số đoạn quy định truy vấn cho MỖI trường. Một hằng dùng chung cho cả lượt nạp trước lẫn
# lượt kiểm tra thật — hai con số khác nhau thì phần nạp trước chấm điểm cho một tập
# đoạn khác với tập sẽ dùng, tức là chấm không.
_PER_FIELD_K = 3


def _context_of(job_prompt: dict) -> str:
    """Ngữ cảnh ghép vào câu truy vấn: tên loại hồ sơ của bộ trường."""
    return str(job_prompt.get("document_kind") or job_prompt.get("display_name") or "").strip()


def regulation_queries(
    fields_catalog: dict, keys: list[str], context: str,
) -> tuple[list[str], list[str]]:
    """(câu truy vấn RAG, khóa trường) cho từng trường cần đối chiếu.

    Câu truy vấn CHỈ ghép từ [loại hồ sơ + nhãn trường + tiêu chí kiểm tra] — KHÔNG dùng
    giá trị OCR. Nhờ vậy nó tính được ngay khi người dùng chọn bộ trường, tức là RAG
    chạy song song với OCR được (`prefetch_field_set_regulations`)."""
    queries: list[str] = []
    out_keys: list[str] = []
    for k in keys:
        entry = fields_catalog.get(k, k)
        q = " ".join(
            p for p in (context, field_label(entry, k), field_check_aspect(entry, "")) if p
        ).strip()
        if q:
            queries.append(q)
            out_keys.append(k)
    if not queries:
        return [context or "quy định"], [""]
    return queries, out_keys


def _regulated_keys(fc: dict) -> list[str]:
    return [k for k in fc if field_check_type(fc.get(k, k)) == "regulated"]


def prefetch_field_set_regulations(job_prompt: dict) -> None:
    """NẠP TRƯỚC đoạn quy định cho bộ trường vừa chọn — gọi NGẦM lúc bắt đầu OCR.

    Lấy SIÊU TẬP các trường có thể phải đối chiếu nên bất kể hồ sơ đọc ra trường nào,
    lượt kiểm tra thật chỉ còn đọc đệm. Embedding + reranker là việc thuần CPU nặng,
    chạy ở đây thì thời gian đó nằm trọn trong lúc OCR."""
    fc = job_prompt.get("fields_catalog", {}) or {}
    keys = _regulated_keys(fc)
    if not keys:
        return
    queries, _ = regulation_queries(fc, keys, _context_of(job_prompt))
    prefetch_regulations(
        field_queries=queries,
        jurisdiction=job_prompt.get("jurisdiction", "VN"),
        # Cùng PHẠM VI và cùng `k` với lượt kiểm tra thật (xem `validate_contract`).
        doc_types=job_prompt.get("doc_types", []),
        per_field_k=_PER_FIELD_K,
        reg_sets=job_prompt.get("regulation_sets") or None,
    )


async def validate_contract(
    contract: dict, job_prompt: dict, selected_fields: list[str], signed_date: str,
    on_progress: Callable[[str], Any] | None = None,
) -> dict:
    """Kiểm tra MỘT contract -> {overall_verdict, checks, violations}. Dùng cho cả 1 file
    lẫn từng file trong phiên đa file."""
    fc = job_prompt.get("fields_catalog", {}) or {}
    fcm = job_prompt.get("field_check_mode", {}) or {}
    selected = set(selected_fields or [])
    always = list(fcm.get("always_check", []))

    ef = contract.get("extracted_fields", {}) or {}

    # DANH SÁCH TRƯỜNG ĐƯA VÀO BÁO CÁO:
    #   - trường KHAI BÁO: luôn hiện, kể cả khi trống (người duyệt cần thấy trường nào
    #     chưa khai để yêu cầu bổ sung);
    #   - trường KIỂM TRA: khi có giá trị và (bắt buộc hoặc được chọn) — trường bắt buộc
    #     còn trống được reconcile_checks bổ sung dưới dạng NEEDS_SUPPLEMENT.
    checked_keys: list[str] = []
    for k in fc:
        entry = fc.get(k, k)
        if field_check_type(entry) == "declaration":
            checked_keys.append(k)
            continue
        if has_value((ef.get(k) or {}).get("value")) and (k in always or k in selected):
            checked_keys.append(k)

    # Chỉ hỏi mô hình cho trường 'regulated' CÓ giá trị — trường trống không có gì để
    # đối chiếu, trường tất định đã có kết luận bằng mã.
    regulated_keys = [k for k in checked_keys if field_check_type(fc.get(k, k)) == "regulated"]

    # GOM TẤT CẢ trường cần đối chiếu vào ĐÚNG 1 LẦN gọi mô hình.
    field_queries, query_keys = regulation_queries(fc, regulated_keys, _context_of(job_prompt))

    if on_progress:
        on_progress("rag")
    # RAG (embedding + truy vấn Chroma) là việc BLOCKING nặng CPU -> chạy trong thread
    # để event loop rảnh đẩy SSE tiến độ.
    _GUARANTEE = 2
    rag_chunks: list = []
    with metrics.stage("rag"):
        # Không có trường nào phải đối chiếu -> không tốn một lượt embedding + Chroma.
        if regulated_keys:
            rag_chunks = await asyncio.to_thread(
                query_regulations_for_fields,
                field_queries=field_queries,
                signed_date=signed_date,
                jurisdiction=job_prompt.get("jurisdiction", "VN"),
                doc_types=job_prompt.get("doc_types", []),
                per_field_k=_PER_FIELD_K,
                guarantee_per_field=_GUARANTEE,
                total_cap=settings.rag_total_cap,
                field_keys=query_keys,
                reg_sets=job_prompt.get("regulation_sets") or None,
            )

    # 1 CÔNG VIỆC / 1 LẦN GỌI: kiểm tra mọi trường regulated trong một lần.
    if on_progress:
        on_progress(f"llm:{len(regulated_keys)}")
    with metrics.stage("llm"):
        if regulated_keys:
            result = await run_validation(job_prompt, contract, regulated_keys, rag_chunks)
        else:
            # KHÔNG CÓ TRƯỜNG NÀO ĐỂ HỎI. Bản cũ bỏ qua bước LLM trong im lặng tuyệt
            # đối: không log, không cờ, báo cáo ra "đạt" với đúng các kết luận tất định
            # — người duyệt không thể phân biệt "đối chiếu xong, không có vi phạm" với
            # "chưa từng đối chiếu điều nào". Hai thứ đó khác nhau về giá trị pháp lý.
            print("[validate] Không có trường 'regulated' nào có giá trị -> BỎ QUA bước "
                  "đối chiếu bằng mô hình. Hồ sơ chỉ còn các kết luận tất định.")
            metrics.bump("llm.skipped_no_fields")
            contract.setdefault("input_flags", []).append({
                "level": "warn", "code": "NO_REGULATED_FIELDS", "field": None,
                "block_field": False, "needs_signed_date": False,
                "message": ("Không trích được trường nào thuộc diện đối chiếu quy định, "
                            "nên bước đối chiếu với văn bản quy định KHÔNG chạy cho hồ sơ này. "
                            "Kết quả dưới đây chỉ gồm các kiểm tra tất định — hãy bổ sung "
                            "tài liệu hoặc điền tay các trường còn trống rồi kiểm tra lại."),
            })
            result = {"checks": [], "overall_verdict": "PASS"}

    # Phần tất định (số nguyên, khai báo, trường bắt buộc) chạy ở đây — báo riêng
    # một bước để người chờ thấy đã qua khỏi đoạn LLM.
    if on_progress:
        on_progress("reconcile")
    with metrics.stage("reconcile"):
        result = reconcile_checks(result, checked_keys, job_prompt, contract, rag_chunks)

    # Trường người dùng CHỌN kiểm tra nhưng hồ sơ trống: không được biến mất khỏi báo cáo.
    seen = {c.get("check_id") for c in result.get("checks", [])}
    for k in sorted(selected - seen):
        if k not in fc or has_value((ef.get(k) or {}).get("value")):
            continue
        entry = fc.get(k, k)
        result["checks"].append({
            "check_id": k, "title": field_label(entry, k), "group": field_group(entry),
            "field_value": None, "severity": "medium", "verdict": "NEEDS_SUPPLEMENT",
            "reason": "Trường được chọn kiểm tra nhưng hồ sơ chưa có giá trị — cần bổ sung "
                      "hoặc nhập tay trên trang soát rồi kiểm tra lại.",
            "reasoning": "", "missing_fields": [k], "fields_used": [k], "citations": [],
        })
    verdicts = [c.get("verdict", "") for c in result.get("checks", [])]
    if not regulated_keys:
        # Không đối chiếu được điều khoản nào thì không thể kết luận "hợp lệ" cho cả hồ sơ.
        verdicts.append("NEEDS_SUPPLEMENT")
    result["overall_verdict"] = overall_verdict(verdicts)

    # Chất lượng truy hồi đo TRƯỚC backfill (đo chính RAG), chất lượng trích dẫn đo
    # SAU reconcile (đo cái người dùng thực sự nhìn thấy trong báo cáo).
    result["_metrics"] = {
        "retrieval": metrics.retrieval_quality(regulated_keys, rag_chunks, _GUARANTEE),
        "citation": metrics.citation_quality(result.get("checks", []), rag_chunks),
    }
    return result
