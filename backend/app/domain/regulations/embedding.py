"""KHO QUY ĐỊNH (embedding) — vector hóa văn bản, ưu tiên SentenceTransformers.

CẢNH BÁO KHÔNG GIAN VECTOR: model dự phòng (ONNX MiniLM, 384 chiều) KHÁC model mặc
định vietnamese-bi-encoder (768 chiều). Seed bằng backend này rồi truy vấn bằng
backend kia -> Chroma báo lệch chiều. Vì vậy: (1) lựa chọn backend được GHI NHỚ ổn
định cho cả phiên; (2) `query` bắt lỗi lệch chiều và báo rõ "seed lại" thay vì 502.
Sửa gốc: cài lại torch (xem requirements.txt).
"""
from __future__ import annotations

from app import metrics
from app.core import settings

_BACKEND = None  # ("st", model) | ("onnx", embedding_function)


class EmbeddingError(RuntimeError):
    """Embedding không dùng được / lệch không gian vector với dữ liệu đã seed."""


def _init_backend():
    """Chọn backend embedding MỘT LẦN cho cả phiên và GHI NHỚ lựa chọn đó.

    Ghi nhớ là bắt buộc chứ không phải tối ưu: hai backend cho ra vector KHÁC SỐ
    CHIỀU (bi-encoder tiếng Việt 768 vs ONNX MiniLM 384). Chọn lại theo từng lượt thì
    cùng một phiên có thể seed bằng backend này rồi truy vấn bằng backend kia — Chroma
    báo lệch chiều, và triệu chứng ('502 khó hiểu') không hề chỉ về nguyên nhân."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(settings.embedding_model)
        _BACKEND = ("st", model)
        print(f"[embedding] Backend: SentenceTransformers ({settings.embedding_model})")
    except Exception as e:
        print("[embedding] Lỗi: " + repr(e))
        from chromadb.utils import embedding_functions
        ef = embedding_functions.DefaultEmbeddingFunction()  # ONNXMiniLM_L6_V2
        _BACKEND = ("onnx", ef)
        # Đếm để báo cáo nêu được: kết quả lượt này chạy trên model DỰ PHÒNG khác
        # không gian vector với dữ liệu đã seed.
        metrics.bump("fallback.embedding_onnx")
    return _BACKEND


def backend_name() -> str:
    """Tên backend embedding đang dùng — vào dấu vân tay kho quy định (`corpus`).

    Chưa khởi tạo thì trả "" chứ KHÔNG tự nạp model: hàm này bị gọi từ endpoint quản
    trị, nạp một model 1GB chỉ để lấy một chuỗi là cái giá không ai muốn trả."""
    if _BACKEND is None:
        return ""
    kind = _BACKEND[0]
    return settings.embedding_model if kind == "st" else "onnx-minilm-l6-v2"


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Trả về list vector đã chuẩn hóa L2."""
    if not texts:
        return []
    kind, impl = _init_backend()
    if kind == "st":
        return impl.encode(texts, normalize_embeddings=True).tolist()

    import numpy as np
    vecs = impl(texts)
    arr = np.asarray(vecs, dtype="float32")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (arr / norms).tolist()


__all__ = ["EmbeddingError", "backend_name", "embed_texts"]
