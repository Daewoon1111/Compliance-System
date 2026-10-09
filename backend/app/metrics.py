"""ĐO LƯỜNG (metrics) — số đo chất lượng + hiệu năng của MỘT lượt kiểm tra hồ sơ.

Vì sao cần: mọi quyết định chỉnh `rag_total_cap`, `num_ctx`, DPI hay bật/tắt reranker
tới nay đều dựa trên quan sát rời rạc trong log. Không có số thì không biết thay đổi
nào có tác dụng, và tệ hơn là không biết một thay đổi đã làm hỏng cái gì.

ĐỌC ĐÚNG TỪNG SỐ — đây là phần dễ bị hiểu sai nhất:

  · `retrieval_coverage_at_k` KHÔNG PHẢI recall@k. Recall@k thật cần nhãn "đoạn quy định
    đúng cho trường này" (golden corpus) mà dự án chưa có. Coverage chỉ trả lời "mỗi
    trường có kéo về được đoạn quy định nào không", tức là bắt được ca RAG trả rỗng, chứ
    KHÔNG nói đoạn kéo về có đúng hay không. Khi có golden corpus thì
    `retrieval_detail` bên dưới đã lưu sẵn chunk_id theo từng trường để tính recall
    thật mà không phải chạy lại hồ sơ.

  · `citation_precision` là số ĐO ĐƯỢC NGAY, không cần nhãn: tỉ lệ trích dẫn trong
    kết luận trỏ tới đoạn quy định THỰC SỰ nằm trong tập đã gửi cho LLM. Nó bắt đúng lỗi
    nguy hiểm nhất của RAG — model bịa số hiệu văn bản. Dưới 1.0 là phải xem lại
    ngay, vì người duyệt tin vào phần căn cứ pháp lý.

  · `latency.p50/p95` tính trên LỊCH SỬ nhật ký kiểm tra, không phải trên một lượt —
    một lượt chỉ có một con số. Lượt hiện tại nằm ở `latency.stages`.

  · `peak_rss_mb` là đỉnh bộ nhớ của CẢ TIẾN TRÌNH, gồm model OCR và embedding đã nạp
    sẵn, nên nó KHÔNG phải chi phí riêng của lượt kiểm tra này.

Bộ đếm ghi ĐỒNG THỜI vào hai chỗ: một Counter mức TIẾN TRÌNH (`counters()`, dùng cho
kiểm thử và soi lâu dài) và một Counter RIÊNG CỦA LƯỢT ĐANG CHẠY giữ trong ContextVar.
Báo cáo đọc Counter riêng, nên hàm gọi sâu trong RAG hay LLM chỉ cần `bump(...)` mà
không phải nhận thêm tham số nào, và hai lượt kiểm tra chạy gối nhau không cộng nhầm
số của nhau.
"""
from __future__ import annotations

import contextvars
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# Bộ đếm toàn tiến trình — chỉ để soi/kiểm thử, KHÔNG dùng tính số của một lượt.
_COUNTERS: Counter[str] = Counter()
_LOCK = threading.Lock()

# Bộ đếm RIÊNG của lượt đang chạy. Trước đây `measure()` lấy phần CHÊNH của `_COUNTERS`
# trước/sau lượt; cách đó chỉ đúng khi mỗi lúc chỉ có một lượt. Hai request /validate
# gối nhau thì phần chênh của người này gồm cả số của người kia, và các bộ đếm mang
# GIÁ TRỊ chứ không phải số lần (`payload.num_ctx_used`) còn bị cộng dồn thành số vô
# nghĩa. ContextVar tách theo lượt giống `_STAGES`, và `asyncio.to_thread` sao chép
# context nên phần chạy trong thread vẫn ghi vào đúng Counter của lượt mình.
_RUN_COUNTERS: contextvars.ContextVar[Counter[str] | None] = contextvars.ContextVar(
    "metrics_run_counters", default=None,
)

# Thời gian từng công đoạn của lượt đang chạy. ContextVar (không phải biến toàn cục)
# để hai lượt kiểm tra chạy đồng thời không cộng nhầm giờ của nhau. `asyncio.to_thread`
# sao chép context nên phần RAG chạy trong thread vẫn ghi vào đúng dict này.
_STAGES: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "metrics_stages", default=None,
)


def bump(name: str, n: int = 1) -> None:
    """Cộng bộ đếm cho CẢ tiến trình lẫn LƯỢT đang chạy. Gọi được từ bất kỳ đâu, kể cả
    trong thread — nhờ vậy hàm sâu trong RAG/LLM không phải nhận thêm tham số chỉ để
    đo đạc."""
    with _LOCK:
        _COUNTERS[name] += n
        if (run := _RUN_COUNTERS.get()) is not None:
            run[name] += n


def counters() -> dict[str, int]:
    """Ảnh chụp bộ đếm MỨC TIẾN TRÌNH (cộng dồn từ lúc khởi động). Số của MỘT lượt
    kiểm tra nằm ở `measure()["counters"]`, không tính từ hàm này."""
    with _LOCK:
        return dict(_COUNTERS)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Cộng thời gian của một công đoạn vào lượt đang chạy (ngoài lượt thì bỏ qua)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        d = _STAGES.get()
        if d is not None:
            d[name] = round(d.get(name, 0.0) + time.perf_counter() - t0, 3)


@contextmanager
def measure() -> Iterator[dict[str, Any]]:
    """Bọc MỘT lượt kiểm tra. Dict trả ra được điền dần và đọc được sau khi thoát."""
    out: dict[str, Any] = {"stages": {}, "counters": {}, "seconds": 0.0}
    mine: Counter[str] = Counter()
    token = _STAGES.set(out["stages"])
    ctoken = _RUN_COUNTERS.set(mine)
    t0 = time.perf_counter()
    try:
        yield out
    finally:
        _STAGES.reset(token)
        _RUN_COUNTERS.reset(ctoken)
        out["seconds"] = round(time.perf_counter() - t0, 3)
        with _LOCK:
            out["counters"] = {k: v for k, v in mine.items() if v}


# ---------------------------------------------------------------------------
# Bộ nhớ tiến trình — KHÔNG thêm phụ thuộc (psutil) chỉ để lấy một con số.
# ---------------------------------------------------------------------------
def peak_rss_mb() -> float | None:
    """Đỉnh RSS của tiến trình (MB). None khi nền không cung cấp — số đo thiếu vẫn tốt
    hơn một số bịa ra."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _MEM(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            info = _MEM()
            info.cb = ctypes.sizeof(_MEM)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(info), info.cb,
            )
            return round(info.PeakWorkingSetSize / 1048576, 1) if ok else None
        except Exception:  # noqa: BLE001 — đo đạc hỏng không được làm hỏng kiểm tra
            return None
    try:
        import resource

        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux trả KB, macOS trả byte.
        return round((kb / 1024 if sys.platform != "darwin" else kb / 1048576), 1)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Chất lượng truy hồi + trích dẫn
# ---------------------------------------------------------------------------
def retrieval_quality(
    regulated_keys: list[str], rag_chunks: list[dict[str, Any]], guarantee: int,
) -> dict[str, Any]:
    """Phủ truy hồi theo TỪNG TRƯỜNG. `field_ranks` do `query_regulations_for_fields`
    gắn sẵn: {field_key: thứ hạng}. Trường không có mặt trong bất kỳ `field_ranks` nào
    là trường LLM sẽ phải kết luận mà không có căn cứ — đúng thứ cần đếm."""
    per_field: dict[str, list[str]] = {k: [] for k in regulated_keys}
    for c in rag_chunks:
        for fk in (c.get("field_ranks") or {}):
            if fk in per_field:
                per_field[fk].append(str(c.get("id") or ""))
    n = len(regulated_keys)
    covered = sum(1 for v in per_field.values() if v)
    full = sum(1 for v in per_field.values() if len(v) >= guarantee)
    return {
        "fields": n,
        "chunks": len(rag_chunks),
        "coverage_at_k": round(covered / n, 3) if n else None,
        "full_coverage_at_k": round(full / n, 3) if n else None,
        "uncovered_fields": sorted(k for k, v in per_field.items() if not v),
        # Giữ lại để tính recall@k THẬT khi có golden corpus, khỏi chạy lại hồ sơ.
        "detail": {k: v for k, v in per_field.items() if v},
    }


def citation_quality(
    checks: list[dict[str, Any]], rag_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Trích dẫn có trỏ đúng đoạn quy định đã gửi không, và bao nhiêu kết luận có căn cứ.

    `grounded` tính trên các kết luận PASS/FAIL — NEEDS_SUPPLEMENT nghĩa là thiếu dữ
    liệu để đối chiếu nên không có căn cứ là đúng, gộp vào sẽ làm số bị pha loãng."""
    known = {str(c.get("id")) for c in rag_chunks if c.get("id")}
    total = valid = auto = static = 0
    decided = grounded = 0
    for ck in checks:
        cits = ck.get("citations") or []
        for ct in cits:
            if not ct.get("chunk_id"):
                static += 1            # căn cứ CỐ ĐỊNH khai trong cấu hình — không phải chunk RAG
                continue
            total += 1
            valid += str(ct.get("chunk_id")) in known
            auto += bool(ct.get("auto_matched"))
        if ck.get("verdict") in ("PASS", "FAIL"):
            decided += 1
            # Căn cứ TỰ GẮN (mô hình không chỉ ra đoạn nào) không tính là có căn cứ.
            grounded += any(not ct.get("auto_matched") for ct in cits)
    return {
        "citations": total,
        "precision": round(valid / total, 3) if total else None,
        "hallucinated": total - valid,
        "auto_matched": auto,
        "static_basis": static,
        "decided_checks": decided,
        "grounded_ratio": round(grounded / decided, 3) if decided else None,
    }


# ---------------------------------------------------------------------------
# Phân vị trên lịch sử
# ---------------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float | None:
    """Phân vị theo nội suy tuyến tính. Ít mẫu vẫn trả số — kèm `samples` để người đọc
    tự biết có tin được không."""
    xs = sorted(v for v in values if isinstance(v, (int, float)))
    if not xs:
        return None
    if len(xs) == 1:
        return round(float(xs[0]), 3)
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return round(float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)), 3)


__all__ = [
    "bump", "citation_quality", "counters", "measure", "peak_rss_mb",
    "percentile", "retrieval_quality", "stage",
]
