"""KHO QUY ĐỊNH (query) — truy vấn theo TỪNG TRƯỜNG: lọc siêu dữ liệu trước, xếp hạng ngữ nghĩa sau.

Thứ tự hai bước đó là điểm phân biệt với một hệ RAG thông thường: bộ lọc `where` loại
trước các đoạn KHÔNG được phép áp cho hồ sơ này (sai phạm vi, sai loại văn bản, chưa/hết
hiệu lực tại ngày ký), rồi mới tới xếp hạng ngữ nghĩa trên phần còn lại.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any

from app import metrics
from app.core import settings

from .dates import date_to_int
from .embedding import EmbeddingError, embed_texts
from .vectorstore import get_collection


def _scope_conditions(jurisdiction: str, doc_types: list[str],
                      reg_sets: list[str] | None = None) -> list[dict[str, Any]]:
    """Điều kiện PHẠM VI — phần KHÔNG BAO GIỜ được bỏ.

    Tách riêng khỏi điều kiện hiệu lực vì hai nhóm có sức nặng khác nhau: thiếu bộ lọc
    ngày thì cùng lắm trích dẫn một bản chưa đúng thời điểm, còn thiếu bộ lọc phạm vi
    thì trích dẫn văn bản không thuộc loại mà bộ trường cho phép."""
    conditions: list[dict[str, Any]] = [{"jurisdiction": {"$eq": jurisdiction}}]
    if doc_types:
        conditions.append({"doc_type": {"$in": doc_types}})
    if reg_sets:
        # Bộ kiểm tra chọn BỘ QUY ĐỊNH -> chỉ đoạn thuộc các bộ đó.
        conditions.append({"reg_set": {"$in": list(reg_sets)}})
    return conditions


def _and(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    """Gộp điều kiện theo cú pháp Chroma (một điều kiện thì KHÔNG bọc `$and`)."""
    return {"$and": conditions} if len(conditions) > 1 else conditions[0]


def _where_clause(signed_date: str, jurisdiction: str, doc_types: list[str],
                  reg_sets: list[str] | None = None) -> dict[str, Any]:
    """Bộ lọc metadata Chroma cho một hồ sơ cụ thể.

    Điểm cốt lõi là lọc theo NGÀY KÝ chứ không phải ngày hôm nay: hợp đồng phải được
    đối chiếu với quy định CÒN HIỆU LỰC TẠI THỜI ĐIỂM KÝ.

    KHÔNG ĐỌC ĐƯỢC NGÀY KÝ -> BỎ HẲN điều kiện hiệu lực, giữ nguyên phạm vi. Bản cũ
    thay bằng mốc dự phòng 19000101; mốc đó nhỏ hơn ngày hiệu lực của MỌI văn bản
    trong kho nên bộ lọc khớp 0 đoạn, và `_query_emb` lặng lẽ truy vấn lại KHÔNG lọc
    gì — mất luôn cả phạm vi. Nói thẳng "không biết ngày ký" ở đây thì phần lọc còn
    lại vẫn giữ được."""
    conditions = _scope_conditions(jurisdiction, doc_types, reg_sets)
    if signed_int := date_to_int(signed_date, default=0):
        conditions += [
            {"effective_from_int": {"$lte": signed_int}},
            {"effective_to_int": {"$gte": signed_int}},
        ]
    return _and(conditions)


# ---------------------------------------------------------------------------
# Reranker (CrossEncoder) — sắp lại đoạn quy định theo độ liên quan với truy vấn.
# ---------------------------------------------------------------------------
_RERANKER: Any = None


def _get_reranker():
    """CrossEncoder dùng chung, hoặc False nếu tắt/không tải được (đã nhớ lựa chọn).

    Trả về False chứ không phải None để phân biệt 'đã quyết định là không dùng' với
    'chưa thử' — nếu không, mỗi lượt truy vấn lại thử tải model ~1GB một lần nữa."""
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER
    if not getattr(settings, "use_reranker", False):
        _RERANKER = False
        return False
    try:
        from sentence_transformers import CrossEncoder
        _RERANKER = CrossEncoder(settings.reranker_model)
        print(f"[rerank] Bật CrossEncoder: {settings.reranker_model}")
    except Exception as e:  # noqa: BLE001 - chưa cài/không tải được -> bỏ rerank
        print("[rerank] Không dùng được reranker, bỏ qua. Chi tiết: " + repr(e))
        _RERANKER = False
    return _RERANKER


# ĐIỂM RERANK theo CẶP [câu truy vấn · đoạn quy định]. Đây là phần đắt nhất của bước RAG
# (168-235 giây mỗi lượt đo trên máy thật) và là phần DUY NHẤT trong chuỗi không phụ
# thuộc bộ lọc: điểm của một cặp không đổi dù hồ sơ ký ngày nào. Đệm theo cặp — thay vì
# theo cả kết quả truy vấn như `_QCACHE` — nên phần nạp trước (chạy song song với OCR,
# lúc chưa biết ngày ký) dùng lại được cho lượt kiểm tra thật.
_RERANK_CACHE: OrderedDict[tuple[str, str], float] = OrderedDict()


def _rerank(query_text: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sắp lại các đoạn theo điểm CrossEncoder (đọc cặp câu hỏi-đoạn cùng lúc, sát
    nghĩa hơn khoảng cách vector). Reranker tắt hoặc lỗi -> trả nguyên thứ tự cũ:
    đây là bước TĂNG chất lượng, hỏng thì mất phần tăng chứ không mất kết quả.

    CHỈ CHẤM cặp chưa có trong đệm. Bộ lọc `where` đổi theo ngày ký nên kết quả truy
    vấn đổi theo, nhưng các đoạn kéo về phần lớn vẫn là những đoạn cũ — chấm lại chúng
    là trả tiền lần thứ hai cho đúng một phép tính."""
    ce = _get_reranker()
    if not ce or len(items) <= 1:
        return items

    khoa = [(query_text, str(it.get("id") or it.get("text", "")[:80])) for it in items]
    with _CACHE_LOCK:
        diem: list[float | None] = [_RERANK_CACHE.get(k) for k in khoa]
    thieu = [i for i, d in enumerate(diem) if d is None]
    metrics.bump("cache.rerank.hit", len(items) - len(thieu))
    metrics.bump("cache.rerank.miss", len(thieu))

    if thieu:
        try:
            moi = ce.predict([(query_text, items[i].get("text", "")) for i in thieu])
        except Exception:  # noqa: BLE001
            return items
        with _CACHE_LOCK:
            for i, d in zip(thieu, moi, strict=True):
                diem[i] = float(d)
                _RERANK_CACHE[khoa[i]] = float(d)
            _trim(_RERANK_CACHE)

    return [items[i] for i in sorted(range(len(items)), key=lambda i: -float(diem[i] or 0.0))]


def _query_emb(q_emb: list[float], where: dict[str, Any] | None, k: int, col,
               where_fallback: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Truy vấn collection bằng MỘT embedding đã tính sẵn. Tách phần embedding ra
    ngoài để caller có thể embed nhiều truy vấn trong 1 lượt (batch).

    `where_fallback` là bộ lọc DỰ PHÒNG dùng khi bộ lọc chính loại hết kết quả — nó
    phải giữ nguyên PHẠM VI (jurisdiction · loại văn bản) và chỉ nới phần
    hiệu lực. Dự phòng bằng `None` là bỏ sạch mọi bộ lọc: hồ sơ có ngày ký đọc trượt sẽ
    nhận cả văn bản ngoài phạm vi, và trong báo cáo trích dẫn đó nhìn không khác gì
    trích dẫn đúng."""
    def _run(where_clause: dict[str, Any] | None):
        return col.query(query_embeddings=[q_emb], n_results=k, where=where_clause,
                         include=["documents", "metadatas", "distances"])

    def _empty(r) -> bool:
        return not r.get("ids") or not r["ids"][0]

    try:
        res = _run(where)
        # Bộ lọc loại hết kết quả -> nới ĐÚNG phần hiệu lực, giữ nguyên phạm vi.
        if _empty(res) and where_fallback is not None and where_fallback != where:
            metrics.bump("rag.where_relaxed")
            res = _run(where_fallback)
    except Exception as e:  # noqa: BLE001
        # Nguyên nhân thường gặp: embedding query LỆCH CHIỀU với vector đã seed (torch
        # lỗi -> rơi về ONNX MiniLM 384 chiều trong khi Chroma seed bằng bi-encoder 768).
        msg = str(e).lower()
        if "dimension" in msg or "dim" in msg or "shape" in msg:
            # Chi tiết kỹ thuật ghi ra log server; người dùng nhận thông báo dễ hiểu.
            print(f"[rag] Lệch chiều embedding (torch lỗi -> rơi về ONNX?): {e!r}")
            raise EmbeddingError(
                "Kho quy định cần được nạp lại trước khi đối chiếu. Hãy nhờ người quản trị "
                "nạp lại kho quy định (trang Quản trị), sau đó kiểm tra lại."
            ) from e
        raise

    # Chroma trả kết quả LỒNG MỘT TẦNG theo số truy vấn (ở đây luôn là 1) — nên đâu
    # cũng là `[0]`. Gỡ tầng đó ra đây, phần còn lại của hệ chỉ thấy danh sách phẳng.
    ids = (res.get("ids") or [[]])[0]
    return [
        {"id": ids[i], "text": res["documents"][0][i], "metadata": res["metadatas"][0][i],
         "distance": float(res["distances"][0][i])}
        for i in range(len(ids))
    ]


# --------------------------------------------------------------------------
# BỘ NHỚ ĐỆM TRUY VẤN — cho phép chạy RAG TRƯỚC, song song với OCR.
# Đệm EMBEDDING (khóa: câu truy vấn). Đây là phần đắt nhất và là phần DUY NHẤT
# không phụ thuộc bộ lọc `where` — ngày ký chỉ đọc được SAU khi OCR xong, nên đệm
# theo where thì nạp trước không dùng lại được, còn đệm theo câu chữ thì dùng được.
# Đệm KẾT QUẢ đã rerank (khóa: câu truy vấn + where + k) — ăn khi duyệt nhiều hồ sơ
# CÙNG bộ trường và cùng ngày ký trong một phiên chạy server.
# --------------------------------------------------------------------------
_EMB_CACHE: OrderedDict[str, Any] = OrderedDict()
_QCACHE: OrderedDict[tuple[str, str, int], list[dict[str, Any]]] = OrderedDict()
_CACHE_MAX = 512
_CACHE_LOCK = threading.Lock()


def _trim(od: OrderedDict) -> None:
    """Giữ đệm ở mức `_CACHE_MAX` mục, bỏ mục CŨ NHẤT trước (FIFO)."""
    while len(od) > _CACHE_MAX:
        od.popitem(last=False)


def _embed_cached(queries: list[str]) -> dict[str, Any]:
    """{câu truy vấn -> vector}. Chỉ encode những câu CHƯA có trong đệm (1 lượt batch)."""
    uniq = list(dict.fromkeys(q for q in queries if q))
    with _CACHE_LOCK:
        todo = [q for q in uniq if q not in _EMB_CACHE]
    metrics.bump("cache.embed.hit", len(uniq) - len(todo))
    metrics.bump("cache.embed.miss", len(todo))
    if todo:
        for q, e in zip(todo, embed_texts(todo), strict=True):
            with _CACHE_LOCK:
                _EMB_CACHE[q] = e
                _trim(_EMB_CACHE)
    with _CACHE_LOCK:
        return {q: _EMB_CACHE[q] for q in uniq if q in _EMB_CACHE}


def _ranked_for_queries(
    queries: list[str], where: dict[str, Any] | None, k: int,
    where_fallback: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """{câu truy vấn -> chunk đã rerank}. Chỉ làm phần CHƯA có đệm."""
    wkey = json.dumps(where, sort_keys=True, ensure_ascii=False, default=str)
    uniq = list(dict.fromkeys(q for q in queries if q))
    out: dict[str, list[dict[str, Any]]] = {}
    todo: list[str] = []
    with _CACHE_LOCK:
        for q in uniq:
            hit = _QCACHE.get((q, wkey, k))
            (out.__setitem__(q, hit) if hit is not None else todo.append(q))
    metrics.bump("cache.query.hit", len(uniq) - len(todo))
    metrics.bump("cache.query.miss", len(todo))
    if todo:
        col = get_collection()
        embs = _embed_cached(todo)          # nạp trước xong thì bước này gần như free
        for q in todo:
            e = embs.get(q)
            if e is None:
                continue
            res = _rerank(q, _query_emb(e, where, k, col, where_fallback))
            out[q] = res
            with _CACHE_LOCK:
                _QCACHE[(q, wkey, k)] = res
                _trim(_QCACHE)
    # Copy NÔNG từng chunk: người gọi gắn thêm 'field_ranks' vào chính dict đó — không
    # tách bản thì lượt sau đọc phải nhãn của hồ sơ trước.
    return {q: [dict(c) for c in out.get(q, [])] for q in uniq}


def clear_query_cache() -> dict[str, int]:
    """XÓA đệm kết quả truy vấn. PHẢI gọi mỗi khi kho quy định được nạp lại.

    `_QCACHE` khóa theo [câu truy vấn · bộ lọc · k] — KHÔNG có phần nào nói tới nội
    dung kho. Nạp lại kho trong CÙNG tiến trình (trang Quản trị sửa một file .md thì
    `seed()` chạy ngay tại chỗ) mà không xóa đệm thì lượt kiểm tra sau vẫn nhận đoạn
    luật CŨ, kèm `chunk_id` đã bị xóa khỏi Chroma — và độ chính xác trích dẫn vẫn báo
    1,0 vì nó so với chính rổ chunk cũ đó, nên sai lệch không lộ ra ở đâu cả.

    KHÔNG xóa `_EMB_CACHE`: nó là vector của CÂU TRUY VẤN, chỉ phụ thuộc model
    embedding chứ không phụ thuộc nội dung kho — xóa đi là ném bỏ phần nạp trước đã
    chạy xong mà không được gì."""
    with _CACHE_LOCK:
        n = len(_QCACHE)
        _QCACHE.clear()
        # Điểm rerank khóa theo `chunk_id`: nạp lại kho có thể sinh ra ĐÚNG id cũ cho
        # một đoạn đã đổi nội dung, nên điểm cũ trở thành điểm của văn bản khác.
        _RERANK_CACHE.clear()
    if n:
        metrics.bump("rag.cache_cleared", n)
        print(f"[rag] Đã xóa {n} kết quả truy vấn trong bộ đệm (kho quy định vừa đổi).")
    return {"queries": n}


def prefetch_regulations(
    field_queries: list[str], jurisdiction: str = "VN",
    doc_types: list[str] | None = None, per_field_k: int = 3,
    reg_sets: list[str] | None = None,
) -> None:
    """NẠP TRƯỚC phần nặng của RAG — gọi NGAY khi biết bộ trường, KHÔNG chờ OCR.

    Truy vấn RAG chỉ cần [loại hồ sơ + nhãn trường]; chỉ có BỘ LỌC ngày ký là phải
    chờ OCR. Nên ở đây làm sẵn BỐN thứ nặng và độc lập với ngày ký:
      1) nạp model embedding + encode toàn bộ câu truy vấn (thuần CPU);
      2) nạp model reranker (~1GB, lần đầu tải/khởi tạo rất lâu);
      3) mở collection ChromaDB;
      4) CHẤM ĐIỂM RERANK cho các đoạn mà truy vấn kéo về — phần đắt nhất còn lại.

    Mục (4) là phần mới. Trước đây nạp trước chỉ lo embedding, nên tỉ lệ dùng lại đệm
    dừng ở 0,5 theo đúng thiết kế và 168-235 giây chấm điểm CrossEncoder vẫn nằm trọn
    trong lúc người dùng ngồi chờ. Ở đây truy vấn bằng bộ lọc CHỈ CÓ PHẠM VI (bỏ điều
    kiện hiệu lực, vì ngày ký chưa đọc được): tập đoạn kéo về gần trùng với lượt thật,
    và điểm rerank đệm theo CẶP [truy vấn · đoạn] nên lượt thật dùng lại được dù bộ
    lọc khi đó có thêm điều kiện ngày ký.

    Lỗi thì im lặng bỏ qua: đây là tối ưu TỐC ĐỘ, hỏng chỉ mất phần nhanh."""
    try:
        qs = [q.strip() for q in field_queries if q and q.strip()]
        if not qs:
            return
        get_collection()
        _get_reranker()
        _embed_cached(qs)
        where_pham_vi = _and(_scope_conditions(jurisdiction, doc_types or [], reg_sets))
        _ranked_for_queries(qs, where_pham_vi, per_field_k)
        print(f"[rag] nạp trước {len(set(qs))} truy vấn quy định + điểm rerank "
              f"({jurisdiction}).")
    except Exception as exc:  # noqa: BLE001
        print(f"[rag] nạp trước bỏ qua: {exc!r}")


def query_regulations_for_fields(
    field_queries: list[str],
    signed_date: str,
    jurisdiction: str,
    doc_types: list[str],
    per_field_k: int = 4,
    guarantee_per_field: int = 2,
    total_cap: int = 48,
    field_keys: list[str] | None = None,
    reg_sets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Truy vấn RAG THEO TỪNG TRƯỜNG rồi gộp lại.

    Một truy vấn gộp chung cho hàng chục trường sẽ chỉ kéo về các đoạn 'chung
    chung' và bỏ sót điều khoản riêng của từng trường. Vì vậy ta truy vấn RIÊNG cho
    mỗi trường (label + check_aspect) — embedding không tốn token LLM, vẫn giữ
    nguyên '1 LLM/1 việc' — rồi gộp & loại trùng theo nội dung để dựng đúng ngữ
    cảnh quy định cho bước kiểm tra (1 lần gọi LLM duy nhất).

    Dùng cho cả trường hợp 1 trường: truyền list 1 phần tử.

    Tối ưu: embed TẤT CẢ truy vấn trong 1 lượt (batch) thay vì gọi model cho từng
    trường -> nhanh hơn nhiều khi có hàng chục trường."""
    pairs = [(q.strip(), (field_keys[i] if field_keys and i < len(field_keys) else ""))
             for i, q in enumerate(field_queries) if q and q.strip()]
    if not pairs:
        return []
    where = _where_clause(signed_date, jurisdiction, doc_types, reg_sets)
    # Dự phòng khi bộ lọc chính rỗng: BỎ hiệu lực, GIỮ phạm vi (xem `_query_emb`).
    where_relaxed = _and(_scope_conditions(jurisdiction, doc_types, reg_sets))

    # Truy vấn từng trường; nếu bật reranker thì sắp lại theo độ liên quan trước khi
    # lấy phần bắt buộc (guarantee_per_field) -> đoạn giữ lại sát nghĩa hơn.
    # per_field: [(field_key, [chunk,...])] — giữ lại field_key để GẮN NHÃN đoạn quy định
    # thuộc về trường nào; bước kiểm tra dùng nhãn này để trích dẫn ĐÚNG trường
    # (gộp chung một rổ thì trường nào cũng có thể lấy trích dẫn của trường khác).
    #
    # QUA BỘ NHỚ ĐỆM (`_QCACHE`): kết quả một truy vấn CHỈ phụ thuộc [câu truy vấn +
    # bộ lọc + k], KHÔNG phụ thuộc giá trị OCR của hồ sơ. Nhờ vậy bước này chạy được
    # NGAY khi biết bộ trường (xem `prefetch_regulations`), song song với
    # OCR — tới lúc bấm kiểm tra thì embedding + reranker (đều thuần CPU, nặng) đã
    # xong và thời gian đó bị giấu hẳn sau OCR.
    ranked = _ranked_for_queries([q for q, _k in pairs], where, per_field_k, where_relaxed)
    per_field: list[tuple[str, list[dict[str, Any]]]] = [
        (f_key, ranked[q_text]) for q_text, f_key in pairs
    ]

    # ĐẢM BẢO PHỦ MỌI TRƯỜNG: GIỮ BẮT BUỘC top `guarantee_per_field` đoạn của MỖI
    # trường (loại trùng theo nội dung), rồi mới cắt theo trần chung. Cắt 'total_cap'
    # đoạn gần nhất trên TOÀN CỤC thì trường có đoạn khớp 'xa' hơn (điều kiện ăn ở,
    # thời giờ làm việc) bị loại sạch và LLM báo 'thiếu quy định' dù luật có. Sau đó
    # mới lấp thêm các đoạn gần nhất còn lại tới total_cap. Không bao giờ bỏ phần bắt buộc.
    chosen: dict[str, dict[str, Any]] = {}

    def _add(it: dict[str, Any], f_key: str, rank: int) -> None:
        key = (it.get("text") or "").strip() or it.get("id")
        cur = chosen.get(key)
        if cur is None:
            cur = chosen[key] = {**it, "field_ranks": {}}
        elif it["distance"] < cur["distance"]:
            cur.update({k: v for k, v in it.items() if k != "field_ranks"})
        # field_ranks: trường nào truy vấn ra đoạn này, ở vị trí thứ mấy (0 = sát nhất).
        if f_key and rank < cur["field_ranks"].get(f_key, 99):
            cur["field_ranks"][f_key] = rank

    for f_key, res in per_field:
        for i, it in enumerate(res[:guarantee_per_field]):
            _add(it, f_key, i)

    extras = sorted(
        ((it, f_key, guarantee_per_field + i)
         for f_key, res in per_field for i, it in enumerate(res[guarantee_per_field:])),
        key=lambda x: x[0]["distance"],
    )
    cap = max(total_cap, len(chosen))  # không cắt mất phần bắt buộc
    for it, f_key, rank in extras:
        # Chạm trần thì THÔI THÊM đoạn mới, nhưng vẫn phải chạy `_add` cho đoạn ĐÃ
        # được chọn: `_add` còn gắn `field_ranks` (đoạn này phục vụ trường nào, hạng
        # mấy). Bản cũ `break` ở đây nên mọi nhãn phía sau bị vứt, và
        # `retrieval_quality` báo độ phủ thấp hơn thực tế.
        if len(chosen) >= cap and ((it.get("text") or "").strip() or it.get("id")) not in chosen:
            continue
        _add(it, f_key, rank)

    return sorted(chosen.values(), key=lambda x: x["distance"])[:cap]


__all__ = ["clear_query_cache", "prefetch_regulations", "query_regulations_for_fields"]
