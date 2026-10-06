"""NGHIỆP VỤ ĐỌC HỒ SƠ (intake) — TIẾP NHẬN một lượt tải hồ sơ lên.

Ba việc:

  1. `resolve_selection` — phân giải THỊ TRƯỜNG · QUỐC GIA · LOẠI HÌNH LAO ĐỘNG mà
     người dùng chọn thành bộ trường 3 tầng dùng cho cả lượt.
  2. `cache_key_of` — khóa cache theo ĐÚNG tập file + lựa chọn (đổi một trong hai là
     phải đọc lại, không được trả kết quả của lựa chọn cũ).
  3. `build_documents` — OCR + trích xuất TỪNG file, rồi phân tích cả BỘ hồ sơ.

Lỗi do người dùng chọn sai ném `SelectionError` (ValueError) — không dùng HTTPException
ở tầng này để domain còn chạy được ngoài web (CLI, test).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.compliance.dossier import analyze_dossier
from app.domain.compliance.quality import signed_date_of
from app.domain.documents.pipeline import process_file
from app.store import (
    file_sha256,
    is_meaningful_text,
    load_markets,
    region_of,
    resolve_country,
    resolve_job_prompt,
    resolve_market,
)

DEFAULT_JOB_ID = "nhat_ban"
GENERIC_JOB_ID = "dong_nam_a"      # bộ trường chung cho thị trường người dùng tự khai


class SelectionError(ValueError):
    """Lựa chọn thị trường/quốc gia/loại hình không hợp lệ — lỗi của NGƯỜI DÙNG."""


@dataclass
class Selection:
    """Một lượt chọn đã phân giải xong: đủ dữ liệu cho cả OCR lẫn ghi nhật ký."""
    job_id: str
    job_prompt: dict[str, Any]
    # KHU VỰC là tầng CHA của lựa chọn (Đông Bắc Á, Đông Nam Á…). Giữ lại sau khi dựng
    # bộ trường để trang 2/3 hiện đúng ở ô "Thị trường": lấy `market_name` thì thị
    # trường một nước (Nhật Bản) làm ô đó trùng luôn ô Quốc gia.
    region: str = ""
    region_name: str = ""
    market: str = ""
    market_name: str = ""
    country: str = ""
    country_name: str = ""
    country_keywords: list[str] = field(default_factory=list)
    job_type: str = ""
    job_type_name: str = ""


def resolve_selection(
    job_id: str = "", market: str = "", country: str = "",
    job_type: str = "", market_other: str = "", job_type_other: str = "",
) -> Selection:
    """Tương thích ngược: client cũ chỉ truyền `job_id` vẫn chạy như trước; `country`
    được phép để trống (thị trường không chia quốc gia, vd Biển quốc tế)."""
    sel = Selection(job_id=job_id, job_prompt={}, market=market, country=country,
                    job_type=job_type)

    if market == "khac":
        if not is_meaningful_text(market_other):
            raise SelectionError("Vui lòng nhập THỊ TRƯỜNG cụ thể.")
        sel.market_name = market_other.strip()
        sel.job_id = job_id or GENERIC_JOB_ID
    elif market:
        mkt = resolve_market(market)
        if not mkt:
            raise SelectionError(f"Thị trường không hợp lệ: {market}")
        sel.market_name = mkt["name"]
        sel.job_id = mkt.get("job_id", GENERIC_JOB_ID)

        ctr = resolve_country(mkt, country)
        if country and not ctr:
            raise SelectionError(f"Quốc gia không thuộc thị trường đã chọn: {country}")
        if ctr:
            sel.country_name = ctr.get("name", "")
            sel.country_keywords = list(ctr.get("keywords") or [])

        if job_type == "khac":
            if not is_meaningful_text(job_type_other):
                raise SelectionError("Vui lòng nhập LOẠI HÌNH LAO ĐỘNG cụ thể.")
            sel.job_type_name = job_type_other.strip()
        elif job_type:
            jt = next((t for t in mkt.get("job_types", []) if t.get("id") == job_type), None)
            sel.job_type_name = jt["name"] if jt else job_type

    sel.job_id = sel.job_id or DEFAULT_JOB_ID
    sel.region, sel.region_name = _region_of(market, country)
    # BỘ TRƯỜNG dựng theo 3 TẦNG: khu vực -> quốc gia -> loại hình lao động.
    # "Công việc trên biển" nhờ vậy là một TẦNG CÔNG VIỆC dùng được ở mọi khu vực.
    try:
        sel.job_prompt = resolve_job_prompt(market, country, job_type, sel.job_id)
    except FileNotFoundError as exc:
        raise SelectionError("Loại công việc không hợp lệ. Vui lòng chọn lại thị "
                             "trường và loại hình lao động.") from exc
    return sel


def _region_of(market: str, country: str) -> tuple[str, str]:
    """(mã, tên) KHU VỰC của một lựa chọn. Không tra được -> ('', '')."""
    rid = region_of(market, country)
    if not rid:
        return "", ""
    name = next((r.get("name", rid) for r in load_markets().get("regions", [])
                 if r.get("id") == rid), rid)
    return rid, str(name)


def cache_key_of(datas: list[bytes], sel: Selection) -> str:
    """Hash gộp NỘI DUNG mọi file + LỰA CHỌN: đổi file hay đổi lựa chọn đều cho khóa
    khác, nên không bao giờ trả lại kết quả của một lựa chọn cũ.

    Hai điều chỉnh so với bản đầu, cả hai đều để khóa đừng đổi khi KHÔNG có gì thật sự
    đổi — khóa trượt nghĩa là OCR lại từ đầu và chạy lại LLM cho đúng bộ hồ sơ vừa
    kiểm xong:

      · Băm TỪNG FILE rồi SẮP XẾP các hàm băm. Bản cũ nối nội dung theo THỨ TỰ TẢI
        LÊN, nên chọn lại đúng bộ file ấy theo thứ tự khác trong hộp thoại là một bộ
        hồ sơ mới dưới mắt hệ thống.
      · KHÔNG đưa `market_name` / `job_type_name` vào khóa. Đó là chuỗi HIỂN THỊ; sửa
        một nhãn trong `markets.json` (thêm dấu cách, đổi cách gọi) là mọi khóa cũ
        chết sạch, trong khi `market` và `job_type` id đã mang đủ ngữ nghĩa."""
    digests = sorted(file_sha256(d) for d in datas)
    return ":".join([
        file_sha256("|".join(digests).encode("utf-8")),
        sel.job_id, sel.market, sel.country, sel.job_type,
    ])


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


def first_signed_date(documents: list[dict[str, Any]]) -> str:
    """NGÀY KÝ sớm nhất dò được (C2 — đối chiếu hiệu lực giấy phép). Ba chỗ có thể
    chứa ngày ký, lấy chỗ nào có trước; không tài liệu nào có -> chuỗi rỗng."""
    for d in documents:
        if signed := signed_date_of(d.get("contract", {}) or {}):
            return signed
    return ""


def merged_fields(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Trường đã trích xuất của CẢ BỘ hồ sơ — tài liệu đầu tiên có giá trị thì thắng.

    Bước phân tích bộ hồ sơ cần vài trường (vd thời hạn hiệu lực giấy phép ở C2) mà
    tài liệu chứa chúng không cố định là file nào; gộp trước rồi tra một lần thì
    không phải mỗi lần kiểm lại đi dò lại qua từng tài liệu."""
    out: dict[str, Any] = {}
    for d in documents:
        for k, f in ((d.get("contract") or {}).get("extracted_fields") or {}).items():
            if k not in out and (f.get("value") if isinstance(f, dict) else f) not in (None, "", [], {}):
                out[k] = f
    return out


async def build_documents(
    session_id: str,
    files: list[tuple[str, bytes]],
    sel: Selection,
    progress_id: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """OCR + trích xuất TỪNG file -> (documents, dossier).

    OCR cả giấy tờ phụ: chúng là nguồn THAM KHẢO để bù trường còn trống ở bước gộp,
    còn ưu tiên trích xuất vẫn thuộc về hợp đồng cung ứng và văn bản đăng ký.
    Lỗi đọc một file được để nguyên cho nơi gọi quyết định mã HTTP."""
    documents: list[dict[str, Any]] = []
    for i, (filename, data) in enumerate(files):
        doc = await process_file(
            session_id, data, filename, sel.job_prompt, sel.job_id,
            sel.market, sel.market_name, sel.job_type, sel.job_type_name,
            pid=progress_id, file_no=i + 1, files_total=len(files),
            country=sel.country, country_name=sel.country_name,
            country_keywords=sel.country_keywords,
            region=sel.region, region_name=sel.region_name,
        )
        doc["contract"].setdefault("contract_meta", {})["session_id"] = session_id
        doc["doc_id"] = f"doc{i + 1}"
        # DUNG LƯỢNG file gốc — ghi lại NGAY ĐÂY vì đây là chỗ duy nhất còn giữ bytes
        # thô; sau bước này chỉ còn text OCR. Trang chỉ số kỹ thuật cần con số này để
        # trả lời "đã kiểm bao nhiêu dữ liệu", và nó cũng là mẫu số hợp lý khi so
        # giây/trang giữa các đợt cấu hình OCR.
        doc["size_bytes"] = len(data)
        documents.append(doc)

    # Phân tích BỘ HỒ SƠ (A3 đủ thành phần + C1 loại giấy phép + C2/C3/C4 hiệu lực).
    dossier = analyze_dossier(
        [{"source_file": d["source_file"], "ocr_text": (d.get("ocr") or {}).get("full_text", "")}
         for d in documents],
        sel.market,
        signed_date=first_signed_date(documents) or None,
        job_type_name=sel.job_type_name,
        job_type_id=sel.job_type,
        extracted_fields=merged_fields(documents),
    )
    return documents, dossier
