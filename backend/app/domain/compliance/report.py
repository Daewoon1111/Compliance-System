"""KIỂM TRA TUÂN THỦ (report) — dựng BÁO CÁO cuối cho một phiên.

Gộp các tài liệu thành MỘT hồ sơ để chỉ kiểm một lần ra một kết luận, chạy đối chiếu,
rồi đóng gói kết quả + siêu dữ liệu cho tầng API trả về.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app import metrics
from app.domain.compliance.quality import signed_date_of
from app.domain.compliance.validation import (
    merge_contracts,
    overall_verdict,
    validate_contract,
)
from app.domain.regulations import corpus
from app.store import load_field_set, load_validation_prompt


def merge_for_check(all_docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Gộp mọi tài liệu của phiên thành MỘT 'document' ảo `merged` để kiểm một lượt.

    Thứ tự TẢI LÊN là thứ tự ưu tiên: tài liệu đầu là tài liệu chính, các tài liệu sau
    chỉ bù trường còn trống."""
    merged = merge_contracts([d.get("contract", {}) for d in all_docs])
    return {
        "doc_id": "merged",
        "source_file": all_docs[0].get("source_file", "") if all_docs else "",
        "contract": merged,
        "missing_fields": merged.get("missing_fields", []),
    }


def request_signature(selected_fields: list[str], signed_date: str = "", context: str = "") -> str:
    """CHỮ KÝ của một yêu cầu kiểm tra: cùng chữ ký -> trả lại báo cáo đã lưu thay vì
    chạy lại mô hình. Gồm phiên bản prompt, NGÀY KÝ đang dùng, tập trường được chọn, và
    VÂN TAY KHO QUY ĐỊNH.

    Ngày ký nằm trong chữ ký vì nó quyết định BỘ VĂN BẢN được đem ra đối chiếu. Vân tay
    kho quy định cũng vậy: sửa một văn bản, đổi model embedding hay prompt kiểm tra mà
    chữ ký không đổi thì hệ trả lại báo cáo cũ và không ai biết nó đã lạc hậu.

    `context`: băm nội dung hồ sơ + bộ trường + cấu hình kiểm tra do nơi gọi tính."""
    version = str(load_validation_prompt().get("version", ""))
    fields = "|".join(sorted(selected_fields))
    return f"{version}#{corpus.corpus_fingerprint()}#{signed_date or ''}#{context}#merged:{fields}"


def job_prompt_of(contract: dict[str, Any]) -> dict[str, Any]:
    """Nạp lại ĐÚNG bộ trường đã dùng lúc trích xuất (mã lưu ở `contract_meta`).
    Bộ trường đã bị xóa -> FileNotFoundError cho nơi gọi trả lỗi rõ ràng."""
    return load_field_set(str((contract.get("contract_meta", {}) or {}).get("field_set_id") or ""))


def _build_metrics(run: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    """Gom số đo của lượt này + phân vị lấy từ NHẬT KÝ các lượt trước."""
    from app.store import latency_percentiles  # noqa: PLC0415 — tránh vòng import

    c = run.get("counters", {}) or {}
    hit = c.get("cache.embed.hit", 0) + c.get("cache.query.hit", 0)
    miss = c.get("cache.embed.miss", 0) + c.get("cache.query.miss", 0)
    retry = {k.rsplit(".", 1)[-1]: v for k, v in c.items() if k.startswith("llm.retry.")}
    qual = res.get("_metrics", {}) or {}
    calls = c.get("payload.calls", 0)
    return {
        "latency": {
            "total_seconds": run.get("seconds"),
            "stages": run.get("stages", {}),
            "history": latency_percentiles(),
        },
        "retrieval": qual.get("retrieval", {}),
        "citation": qual.get("citation", {}),
        "payload": {
            "calls": calls,
            "chars": c.get("payload.chars", 0),
            "tokens_est": c.get("payload.chars", 0) // 3,
            "fields": round(c.get("payload.fields", 0) / calls, 1) if calls else None,
            "chunks": round(c.get("payload.chunks", 0) / calls, 1) if calls else None,
            "num_ctx_used": round(c.get("payload.num_ctx_used", 0) / calls) if calls else None,
            "num_ctx_config": round(c.get("payload.num_ctx_config", 0) / calls) if calls else None,
        },
        "cache": {
            "hit": hit, "miss": miss,
            "hit_ratio": round(hit / (hit + miss), 3) if (hit + miss) else None,
        },
        "resilience": {
            "retry": retry,
            "retry_total": sum(retry.values()),
            "llm_fallback_model": c.get("llm.fallback_model", 0),
            "embedding_fallback_onnx": c.get("fallback.embedding_onnx", 0),
        },
        "peak_rss_mb": metrics.peak_rss_mb(),
    }


async def build_report(
    doc: dict[str, Any],
    job_prompt: dict[str, Any],
    selected_fields: list[str],
    req_sig: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Chạy đối chiếu rồi đóng gói báo cáo. Lỗi mô hình/RAG được ném NGUYÊN cho nơi gọi:
    mỗi loại lỗi cần một mã HTTP và một câu hướng dẫn khác nhau."""
    contract = doc["contract"]
    # Đọc trượt thì để chuỗi RỖNG chứ KHÔNG bịa mốc: rỗng nói đúng sự thật "không biết
    # ngày ký" và bộ lọc quy định xử lý nó tường minh (không lọc theo hiệu lực).
    signed_date = signed_date_of(contract)

    with metrics.measure() as run:
        res = await validate_contract(contract, job_prompt, selected_fields, signed_date,
                                      on_progress=on_progress)
    checks = res.get("checks", [])
    out_doc = {
        "doc_id": doc["doc_id"],
        "source_file": doc.get("source_file", ""),
        "overall_verdict": res.get("overall_verdict", "NEEDS_SUPPLEMENT"),
        "checks": checks,
        "violations": [c for c in checks if c.get("verdict") == "FAIL"],
        "input_flags": contract.get("input_flags", []) or [],
    }
    return {
        "documents": [out_doc],
        "metrics": _build_metrics(run, res),
        "overall_verdict": overall_verdict([out_doc["overall_verdict"]]),
        "field_set_id": job_prompt.get("id", ""),
        "field_set_name": job_prompt.get("display_name", ""),
        "document_kind": job_prompt.get("document_kind", ""),
        "signed_date": signed_date,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        # `corpus_fingerprint`: lệch với kho hiện tại nghĩa là căn cứ đã đổi kể từ lúc
        # kết luận này được sinh ra. `cached` phân biệt báo cáo VỪA CHẠY với báo cáo
        # lấy lại từ đĩa — hai thứ trông y hệt nhau trên màn hình.
        "_meta": {"req_sig": req_sig, "corpus_fingerprint": corpus.corpus_fingerprint(),
                  "cached": False},
    }


__all__ = ["build_report", "job_prompt_of", "merge_for_check", "request_signature"]
