"""NGHIỆP VỤ KIỂM TRA (validation) — orchestrator validate_contract: RAG -> LLM đối chiếu -> reconcile; re-export API con.

Module con trong nhánh kiểm tra:
  payload.py    — dựng payload + VALIDATION_SCHEMA + gọi LLM (run_validation)
  reconcile.py  — reconcile_checks / merge_contracts / verdict / duration
  validation.py — validate_contract (RAG -> LLM -> reconcile) + re-export API con
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
    duration_to_months,
    extract_duration,
    field_check_type,
    field_group,
    fill_default_currency,
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
    "duration_to_months",
    "extract_duration",
    "field_check_type",
    "field_group",
    "has_value",
    "merge_contracts",
    "overall_verdict",
    "prefetch_market_regulations",
    "reconcile_checks",
    "regulation_queries",
    "run_validation",
    "validate_contract",
]


# Số đoạn luật truy vấn cho MỖI trường. Một hằng dùng chung cho cả lượt nạp trước lẫn
# lượt kiểm tra thật — hai con số khác nhau thì phần nạp trước chấm điểm cho một tập
# đoạn khác với tập sẽ dùng, tức là chấm không.
_PER_FIELD_K = 3


def _field_group(fields_catalog: dict, key: str) -> str:
    """Nhóm hiển thị của trường `key` trong catalog (bọc `field_group` cho tiện tra)."""
    return field_group(fields_catalog.get(key, key))


def regulation_queries(
    fields_catalog: dict, keys: list[str], market: str,
) -> tuple[list[str], list[str]]:
    """(câu truy vấn RAG, khóa trường) cho từng trường cần đối chiếu.

    Câu truy vấn CHỈ ghép từ [thị trường + nhãn trường + khía cạnh kiểm tra] — KHÔNG
    dùng giá trị OCR. Nhờ vậy nó tính được ngay khi người dùng chọn thị trường, tức
    là RAG chạy song song với OCR được (`prefetch_market_regulations`)."""
    queries: list[str] = []
    out_keys: list[str] = []
    for k in keys:
        entry = fields_catalog.get(k, k)
        q = " ".join(
            p for p in (market, field_label(entry, k), field_check_aspect(entry, "")) if p
        ).strip()
        if q:
            queries.append(q)
            out_keys.append(k)
    if not queries:
        return ["quy định liên quan hợp đồng cung ứng lao động"], [""]
    return queries, out_keys


def prefetch_market_regulations(job_prompt: dict, market: str) -> None:
    """NẠP TRƯỚC đoạn luật của thị trường vừa chọn — gọi NGẦM lúc bắt đầu OCR.

    Lấy SIÊU TẬP các trường có thể phải đối chiếu (mọi trường 'regulated' không thuộc
    nhóm chi phí) nên bất kể hồ sơ đọc ra trường nào, lượt kiểm tra thật cũng chỉ còn
    đọc đệm. Embedding + reranker là việc thuần CPU nặng, chạy ở đây thì thời gian đó
    nằm trọn trong lúc OCR đang chạy."""
    fc = job_prompt.get("fields_catalog", {}) or {}
    keys = [k for k in fc
            if field_check_type(fc.get(k, k)) == "regulated"
            and _field_group(fc, k) != "payer"]
    queries, _ = regulation_queries(fc, keys, market)
    prefetch_regulations(
        field_queries=queries,
        jurisdiction=job_prompt.get("jurisdiction", "VN"),
        market_id=job_prompt.get("job_id", ""),
        # Cùng PHẠM VI và cùng `k` với lượt kiểm tra thật (xem `validate_contract`):
        # lệch một trong hai là đoạn kéo về khác đi và điểm rerank đã chấm thành vô ích.
        doc_types=job_prompt.get("doc_types", []),
        per_field_k=_PER_FIELD_K,
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

    # Khoản chi phí bằng 0 nhưng rụng đơn vị -> gắn đơn vị tiền của thị trường.
    fill_default_currency(contract, fc)
    ef = contract.get("extracted_fields", {}) or {}

    # DANH SÁCH TRƯỜNG ĐƯA VÀO BÁO CÁO:
    #   - nhóm 'khai báo' và nhóm 'các chi phí': LUÔN hiện, kể cả khi giá trị TRỐNG
    #     (người duyệt cần thấy trường nào chưa khai để yêu cầu bổ sung);
    #   - nhóm 'kiểm tra': chỉ khi có giá trị (trường bắt buộc còn trống đã được
    #     reconcile_checks bổ sung dưới dạng NEEDS_SUPPLEMENT).
    checked_keys: list[str] = []
    for k in fc:
        entry = fc.get(k, k)
        ct = field_check_type(entry)
        if _field_group(fc, k) in ("declaration", "payer"):
            checked_keys.append(k)
            continue
        if not has_value((ef.get(k) or {}).get("value")):
            continue
        if ct == "declaration" or k in always or k in selected:
            checked_keys.append(k)

    # Chỉ hỏi LLM cho trường 'regulated' CÓ giá trị — trường trống không có gì để đối chiếu.
    # LOẠI TRỪ những trường đã có kết luận TẤT ĐỊNH ở reconcile_checks; hỏi LLM về
    # chúng là trả tiền token cho câu trả lời sẽ bị vứt đi:
    #   · nhóm 'payer' (các khoản chi phí) -> check_payer_cost quyết định HỢP LỆ/KHÔNG
    #     hoàn toàn bằng luật whitelist + số tiền. Đây là phần LỚN NHẤT: riêng thị
    #     trường Nhật Bản là 16/27 trường regulated (đo 22/07) — bỏ đi thì cắt cả phần
    #     mô tả trong prompt lẫn 16 mục JSON model phải sinh ra.
    #   · check_type 'positive_integer'/'factual_location'/'declaration'/
    #     'deferred_foreign' -> đã không lọt vào đây vì khác 'regulated'.
    regulated_keys = [
        k for k in checked_keys
        if field_check_type(fc.get(k, k)) == "regulated"
        and _field_group(fc, k) != "payer"
        and has_value((ef.get(k) or {}).get("value"))
    ]

    _cmeta = contract.get("contract_meta", {}) or {}
    market = " ".join(
        p for p in (_cmeta.get("market_name"), _cmeta.get("job_type_name")) if p
    ).strip() or (job_prompt.get("display_name") or "").strip()

    # GOM TẤT CẢ trường cần đối chiếu vào ĐÚNG 1 LẦN gọi LLM DUY NHẤT (tiết kiệm
    # token). Việc tách kết luận chung / kết luận chi phí vẫn thực hiện ở
    # reconcile_checks theo field_group, KHÔNG cần gọi LLM thêm lần nào.
    field_queries, query_keys = regulation_queries(fc, regulated_keys, market)

    if on_progress:
        on_progress("rag")
    # RAG (embedding + truy vấn Chroma) là việc BLOCKING nặng CPU -> chạy trong thread
    # để event loop rảnh đẩy SSE tiến độ.
    _GUARANTEE = 2
    with metrics.stage("rag"):
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
            market_id=job_prompt.get("job_id", ""),   # #1 lọc điều khoản đúng thị trường
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
                  "đối chiếu LLM. Hồ sơ chỉ còn các kết luận tất định.")
            metrics.bump("llm.skipped_no_fields")
            contract.setdefault("input_flags", []).append({
                "level": "warn", "code": "NO_REGULATED_FIELDS", "field": None,
                "block_field": False, "needs_signed_date": False,
                "message": ("Không trích được trường nào thuộc diện đối chiếu quy định, "
                            "nên bước đối chiếu với văn bản luật KHÔNG chạy cho hồ sơ này. "
                            "Kết quả dưới đây chỉ gồm các kiểm tra tất định — hãy bổ sung "
                            "tài liệu hoặc điền tay các trường còn trống rồi kiểm tra lại."),
            })
            result = {"checks": [], "overall_verdict": "PASS"}

    # Phần tất định (chi phí, số nguyên, địa điểm, khai báo) chạy ở đây — báo riêng
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
            "check_id": k, "title": field_label(entry, k), "group": _field_group(fc, k),
            "field_value": None, "severity": "medium", "verdict": "NEEDS_SUPPLEMENT",
            "reason": "Trường được chọn kiểm tra nhưng hồ sơ chưa có giá trị — cần bổ sung "
                      "hoặc nhập tay trên trang soát rồi kiểm tra lại.",
            "reasoning": "", "missing_fields": [k], "fields_used": [k], "citations": [],
        })
    verdicts = [c.get("verdict", "") for c in result.get("checks", [])]
    if not regulated_keys:
        # Không đối chiếu được điều luật nào thì không thể kết luận "hợp lệ" cho cả hồ sơ.
        verdicts.append("NEEDS_SUPPLEMENT")
    result["overall_verdict"] = overall_verdict(verdicts)

    # Chất lượng truy hồi đo TRƯỚC backfill (đo chính RAG), chất lượng trích dẫn đo
    # SAU reconcile (đo cái người dùng thực sự nhìn thấy trong báo cáo).
    result["_metrics"] = {
        "retrieval": metrics.retrieval_quality(regulated_keys, rag_chunks, _GUARANTEE),
        "citation": metrics.citation_quality(result.get("checks", []), rag_chunks),
    }
    return result
