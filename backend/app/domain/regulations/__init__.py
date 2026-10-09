"""NGHIỆP VỤ KHO QUY ĐỊNH — embedding · ChromaDB · ingest văn bản quy định · truy vấn RAG.

Sáu module con xếp theo chiều phụ thuộc (module trên không biết gì về module dưới):

  - `dates`        quy đổi ngày sang số để Chroma lọc khoảng hiệu lực.
  - `embedding`    vector hóa văn bản + xử lý lệch không gian vector.
  - `vectorstore`  vòng đời ChromaDB: client · collection · dọn segment mồ côi.
  - `ingest`       cắt đoạn · gắn metadata · nạp vào kho.
  - `query`        lọc siêu dữ liệu trước, xếp hạng ngữ nghĩa sau + bộ nhớ đệm.
  - `corpus`       QUẢN TRỊ kho quy định: nguồn · phiên bản · hiệu lực · hàm băm · phê duyệt.
  - `seed`         nạp toàn bộ `app/rules/*.md` (npm run seed).

Module này chỉ RE-EXPORT để `from app.domain.regulations import ...` giữ nguyên.
"""
from __future__ import annotations

from . import corpus
from .dates import OPEN_END_DATE, date_to_int
from .embedding import EmbeddingError, embed_texts
from .ingest import chunk_markdown, ingest_markdown_text
from .query import (
    _where_clause,
    clear_query_cache,
    prefetch_regulations,
    query_regulations_for_fields,
)
from .seed import seed
from .vectorstore import (
    get_client,
    get_collection,
    prune_orphan_segments,
    reset_collection,
    upsert_chunks,
)

__all__ = [
    "OPEN_END_DATE", "EmbeddingError",
    "_where_clause", "chunk_markdown", "clear_query_cache", "corpus", "date_to_int",
    "embed_texts", "get_client", "get_collection", "ingest_markdown_text",
    "prefetch_regulations", "prune_orphan_segments", "query_regulations_for_fields",
    "reset_collection", "seed", "upsert_chunks",
]
