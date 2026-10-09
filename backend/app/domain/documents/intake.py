"""NGHIỆP VỤ ĐỌC HỒ SƠ (intake) — TIẾP NHẬN một lượt tải hồ sơ lên.

Ba việc:

  1. `resolve_selection` — phân giải BỘ TRƯỜNG người dùng chọn (loại hồ sơ).
  2. `cache_key_of` — khóa cache theo ĐÚNG tập file + bộ trường (đổi một trong hai là
     phải đọc lại, không được trả kết quả của lựa chọn cũ).
  3. `build_documents` — OCR + trích xuất TỪNG file.

Lỗi do người dùng chọn sai ném `SelectionError` (ValueError) — không dùng HTTPException
ở tầng này để domain còn chạy được ngoài web (CLI, test).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.domain.documents.pipeline import process_file
from app.store import file_sha256, load_field_set


class SelectionError(ValueError):
    """Bộ trường không hợp lệ — lỗi của NGƯỜI DÙNG."""


@dataclass
class Selection:
    """Một lượt chọn đã phân giải xong."""
    field_set_id: str
    field_set_name: str
    job_prompt: dict[str, Any]


def resolve_selection(field_set_id: str) -> Selection:
    if not (field_set_id or "").strip():
        raise SelectionError("Chưa có bộ kiểm tra — hãy tạo bộ kiểm tra trước.")
    try:
        fs = load_field_set(field_set_id.strip())
    except FileNotFoundError as exc:
        raise SelectionError(f"Không có bộ trường '{field_set_id}'. Hãy chọn lại.") from exc
    if not fs.get("fields_catalog"):
        raise SelectionError("Bộ trường đã chọn chưa có trường nào.")
    return Selection(field_set_id=fs["id"], field_set_name=str(fs.get("display_name") or fs["id"]),
                     job_prompt=fs)


def parse_regions(raw: str, n_files: int) -> list[dict[str, Any] | None]:
    """VÙNG CẦN KIỂM TRA gửi kèm lượt tải lên (JSON) -> danh sách theo thứ tự file.

    Dạng vào: `[{"skip": [chỉ số trang], "rects": {"<trang>": [x0, y0, x1, y1]}} | null, ...]`
    — tọa độ chuẩn hóa 0..1, gốc trên-trái. Vùng được kẹp vào [0, 1] và sắp lại hai góc;
    vùng quá nhỏ (< 1% mỗi chiều) bị bỏ. Sai dạng -> SelectionError."""
    if not (raw or "").strip():
        return [None] * n_files
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SelectionError("Vùng cần kiểm tra không hợp lệ — hãy chọn vùng lại.") from exc
    if not isinstance(data, list) or len(data) > n_files:
        raise SelectionError("Vùng cần kiểm tra không khớp danh sách file.")
    out: list[dict[str, Any] | None] = []
    for item in data + [None] * (n_files - len(data)):
        if not item:
            out.append(None)
            continue
        if not isinstance(item, dict):
            raise SelectionError("Vùng cần kiểm tra không hợp lệ.")
        try:
            skip = sorted({int(i) for i in (item.get("skip") or [])})
            rects: dict[int, tuple[float, float, float, float]] = {}
            for k, r in (item.get("rects") or {}).items():
                x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in r)
                x0, x1 = sorted((x0, x1))
                y0, y1 = sorted((y0, y1))
                if x1 - x0 >= 0.01 and y1 - y0 >= 0.01:
                    rects[int(k)] = (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))
        except (TypeError, ValueError) as exc:
            raise SelectionError("Vùng cần kiểm tra không hợp lệ.") from exc
        if any(i < 0 for i in skip) or any(i < 0 for i in rects):
            raise SelectionError("Số trang trong vùng cần kiểm tra không hợp lệ.")
        out.append({"skip": skip, "rects": rects} if (skip or rects) else None)
    return out


def cache_key_of(datas: list[bytes], sel: Selection,
                 regions: list[dict[str, Any] | None] | None = None) -> str:
    """Hash gộp NỘI DUNG mọi file + NỘI DUNG bộ trường.

    Băm TỪNG FILE rồi SẮP XẾP các hàm băm: chọn lại đúng bộ file theo thứ tự khác vẫn là
    cùng một bộ hồ sơ. Băm cả nội dung bộ trường (không chỉ mã): sửa nhãn một trường
    phải làm lượt tải lên sau trích xuất lại, không được trả kết quả dựng trên bản cũ."""
    # Vùng cần kiểm tra đi CÙNG file của nó (đổi thứ tự file không đổi khóa, đổi vùng thì đổi).
    regs = regions or [None] * len(datas)
    digests = sorted(
        file_sha256(d) + (":" + json.dumps(r, sort_keys=True, default=list) if r else "")
        for d, r in zip(datas, regs, strict=True))
    fs_hash = hashlib.sha256(
        json.dumps(sel.job_prompt, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    ).hexdigest()[:16]
    return ":".join([file_sha256("|".join(digests).encode("utf-8")), sel.field_set_id, fs_hash])


def summarize(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Dạng rút gọn trả về cho trang soát dữ liệu (bỏ `ocr` — nặng và chưa cần)."""
    meta = documents[0]["contract"].get("contract_meta", {}) if documents else {}
    return {
        "session_id": meta.get("session_id", ""),
        "documents": [
            {
                "doc_id": d.get("doc_id"),
                "source_file": d.get("source_file"),
                "extracted_json": d.get("contract"),
                "missing_fields": d.get("missing_fields", []),
            }
            for d in documents
        ],
    }


async def build_documents(
    session_id: str,
    files: list[tuple[str, bytes]],
    sel: Selection,
    progress_id: str = "",
    regions: list[dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """OCR + trích xuất TỪNG file. Thứ tự tải lên là thứ tự ưu tiên khi gộp: tài liệu
    đầu tiên là tài liệu chính, các tài liệu sau bù trường còn trống.
    Lỗi đọc một file được để nguyên cho nơi gọi quyết định mã HTTP."""
    documents: list[dict[str, Any]] = []
    for i, (filename, data) in enumerate(files):
        doc = await process_file(session_id, data, filename, sel.job_prompt,
                                 pid=progress_id, file_no=i + 1, files_total=len(files),
                                 region=(regions or [None] * len(files))[i])
        doc["contract"].setdefault("contract_meta", {})["session_id"] = session_id
        doc["doc_id"] = f"doc{i + 1}"
        # DUNG LƯỢNG file gốc — chỗ duy nhất còn giữ bytes thô; trang chỉ số cần nó.
        doc["size_bytes"] = len(data)
        documents.append(doc)
    return documents
