"""KHO QUY ĐỊNH (ingest) — cắt văn bản luật thành đoạn, gắn metadata, nạp vào kho.

Metadata quyết định đoạn nào ĐƯỢC PHÉP xuất hiện khi đối chiếu một hồ sơ, nên phần
dựng metadata nằm trọn ở đây chứ không rải ra chỗ gọi.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .dates import OPEN_END_DATE, date_to_int
from .embedding import embed_texts
from .vectorstore import upsert_chunks

# Thị trường có ĐIỀU KHOẢN RIÊNG trong luật (TT02/2024, NĐ112): mỗi khối quy định
# chỉ áp cho đúng thị trường của nó. Tên (bỏ dấu, chữ thường) để nhận diện chunk.
MARKET_MARKERS: dict[str, list[str]] = {
    "nhat_ban": ["nhat ban"],
    "dai_loan": ["dai loan"],
    "han_quoc": ["han quoc"],
    "macao": ["macao", "ma cao"],
}
MARKET_GENERIC = "chung"   # áp cho MỌI thị trường (luật chung, không nêu riêng nước nào)


def market_of_chunk(text: str) -> str:
    """Gắn NHÃN THỊ TRƯỜNG cho một đoạn luật theo tên nước nó nói tới.

    Đoạn nêu ĐÚNG MỘT thị trường (vd điều khoản 'Thuyền viên tàu cá gần bờ (thị thực
    E10)' của Hàn Quốc) -> nhãn thị trường đó -> KHÔNG bị trích dẫn nhầm cho hồ sơ nước
    khác. Đoạn không nêu nước nào, hoặc nêu ≥2 nước (điều khoản chung/so sánh) -> 'chung'
    (áp cho mọi thị trường). Không cần dò ranh giới mục — bền với OCR/format markdown."""
    from app.domain.documents.ocr import fold_diacritics
    folded = fold_diacritics(text or "").lower()
    hit = {m for m, kws in MARKET_MARKERS.items() if any(k in folded for k in kws)}
    return next(iter(hit)) if len(hit) == 1 else MARKET_GENERIC


def chunk_markdown(md: str, max_chars: int = 1200) -> list[str]:
    """Cắt văn bản luật thành đoạn <= `max_chars`, GHÉP THEO ĐOẠN VĂN.

    Cắt theo ranh giới đoạn (dòng trống) chứ không cắt cứng theo số ký tự: một khoản
    luật bị chẻ làm đôi giữa câu thì cả hai nửa đều mất nghĩa khi đem đi so vector, và
    trích dẫn hiện cho người duyệt cũng thành câu cụt. Đoạn dài hơn `max_chars` được
    giữ NGUYÊN VẸN thành một chunk riêng — thà chunk to còn hơn chunk vô nghĩa."""
    paras = [p.strip() for p in md.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= max_chars:
            cur = (cur + "\n\n" + p).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def chunk_ids(source_doc: str, chunks: list[str]) -> list[str]:
    """ID xác định theo (source_doc + chỉ số + nội dung).

    Nạp lại CÙNG nội dung sẽ upsert đè đúng chỗ thay vì tạo bản sao mới; ID ngẫu nhiên
    (uuid4) thì mỗi lần seed lại sinh một bộ bản sao."""
    return [
        f"{source_doc}::{idx}::{hashlib.sha1(c.encode('utf-8')).hexdigest()[:12]}"
        for idx, c in enumerate(chunks)
    ]


def ingest_markdown_text(
    md_text: str,
    source_doc: str,
    jurisdiction: str,
    doc_type: str,
    effective_from: str,
    effective_to: str | None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Nạp MỘT văn bản luật: cắt đoạn -> embed -> gắn metadata -> upsert.

    Metadata quyết định đoạn nào ĐƯỢC PHÉP xuất hiện khi đối chiếu một hồ sơ:
      · `jurisdiction` / `doc_type` — phạm vi và loại văn bản;
      · `effective_from_int` / `effective_to_int` — hiệu lực dạng SỐ, vì Chroma chỉ so
        sánh được số với `$lte`/`$gte` (chuỗi ngày đi kèm để hiển thị trích dẫn);
      · `market` — thị trường của chính điều khoản (xem `market_of_chunk`);
      · `extra_metadata` — phần QUẢN TRỊ KHO LUẬT (số hiệu, phiên bản, hàm băm, người
        phê duyệt) do `corpus.py` cấp; đi kèm từng đoạn để người duyệt truy được đoạn
        trích dẫn về đúng bản văn bản đã phê duyệt.

    `effective_to=None` = còn hiệu lực -> lưu sentinel `OPEN_END_DATE`; Chroma không
    nhận giá trị None trong metadata nên không thể để trống.

    Trả {"inserted": số chunk, "source_doc": tên hiển thị}."""
    chunks = chunk_markdown(md_text)
    embeddings = embed_texts(chunks)

    eff_to = effective_to if effective_to else OPEN_END_DATE
    eff_from_int = date_to_int(effective_from, default=0)
    eff_to_int = date_to_int(eff_to, default=99991231)
    extra = {k: v for k, v in (extra_metadata or {}).items() if v not in (None, "")}

    metadatas: list[dict[str, Any]] = [
        {
            "source_doc": source_doc,
            "jurisdiction": jurisdiction,
            "doc_type": doc_type,
            "market": market_of_chunk(chunk),   # #1 lọc theo thị trường của điều khoản
            "effective_from": effective_from,    # chuỗi để hiển thị/trích dẫn
            "effective_to": eff_to,
            "effective_from_int": eff_from_int,  # số để lọc khoảng ngày
            "effective_to_int": eff_to_int,
            "chunk_index": idx,
            **extra,
        }
        for idx, chunk in enumerate(chunks)
    ]

    upsert_chunks(ids=chunk_ids(source_doc, chunks), texts=chunks,
                  embeddings=embeddings, metadatas=metadatas)
    return {"inserted": len(chunks), "source_doc": source_doc}


__all__ = [
    "MARKET_GENERIC", "MARKET_MARKERS", "chunk_ids", "chunk_markdown",
    "ingest_markdown_text", "market_of_chunk",
]
