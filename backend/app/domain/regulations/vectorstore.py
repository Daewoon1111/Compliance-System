"""KHO QUY ĐỊNH (vectorstore) — vòng đời ChromaDB: client · collection · dọn rác đĩa.

Chỉ ở đây mới `import chromadb`. Nhờ vậy phần ingest và phần truy vấn nói chuyện với
kho qua bốn hàm, và bài kiểm thử thay được cả tầng lưu trữ bằng một đối tượng giả.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import sqlite3
from typing import Any

from app.core import settings

_CLIENT = None

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def get_client():
    """Client ChromaDB bền trên đĩa (`settings.chroma_persist_dir`), dựng một lần.

    Import `chromadb` nằm TRONG hàm: gói này nặng và chỉ cần khi thật sự chạm kho quy
    định, nên nạp trễ giữ thời gian khởi động backend ngắn."""
    global _CLIENT
    if _CLIENT is None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        _CLIENT = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _CLIENT


def get_collection():
    """Collection quy định (`settings.chroma_collection`), tự tạo nếu chưa có."""
    return get_client().get_or_create_collection(name=settings.chroma_collection)


def reset_collection() -> None:
    """Xóa sạch collection trước khi nạp lại — tránh tích lũy bản sao khi seed nhiều lần.

    Xóa luôn ĐỆM TRUY VẤN của `query`: đệm đó giữ nguyên văn các đoạn quy định cũ và
    `chunk_id` sắp bị xóa, nên nạp lại kho mà để đệm sống là hệ vẫn đối chiếu theo bản
    luật cũ trong suốt phần đời còn lại của tiến trình. Đặt ở ĐÂY chứ không ở `seed()`
    để mọi đường nạp lại — dòng lệnh `npm run seed` lẫn trang Quản trị — đều đi qua."""
    client = get_client()
    with contextlib.suppress(Exception):
        client.delete_collection(name=settings.chroma_collection)   # chưa có -> bỏ qua
    client.get_or_create_collection(name=settings.chroma_collection)
    from .query import clear_query_cache  # noqa: PLC0415 - nạp trễ, tránh vòng import

    clear_query_cache()


def prune_orphan_segments() -> int:
    """Xóa các thư mục segment (vector index) MỒ CÔI trên đĩa — không còn được
    chroma.sqlite3 tham chiếu (rác tích lũy sau nhiều lần reset/seed). Trả số thư mục đã xóa.

    An toàn: chỉ xóa thư mục tên UUID KHÔNG nằm trong bảng 'segments' của DB hiện tại.
    """
    base = settings.chroma_persist_dir
    db = os.path.join(base, "chroma.sqlite3")
    if not os.path.isdir(base) or not os.path.exists(db):
        return 0

    keep: set[str] = set()
    try:
        con = sqlite3.connect(db)
        try:
            keep = {str(sid) for (sid,) in con.execute("SELECT id FROM segments")}
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return 0  # không đọc được -> KHÔNG xóa gì (an toàn)

    removed = 0
    for name in os.listdir(base):
        d = os.path.join(base, name)
        if os.path.isdir(d) and _UUID_RE.match(name) and name not in keep:
            try:
                shutil.rmtree(d)
                removed += 1
            except OSError:
                pass
    return removed


def prune_orphan_rows() -> int:
    """Xóa các HÀNG mồ côi trong `chroma.sqlite3` — vector của những collection đã bị
    `reset_collection()` xóa. Trả số hàng `embeddings` đã bỏ.

    Vì sao cần: `delete_collection` của Chroma gỡ collection khỏi bảng `segments`
    nhưng KHÔNG dọn các hàng `embeddings` / `embedding_metadata` thuộc segment đó. Mỗi
    lần `npm run seed` vì thế để lại nguyên một bộ bản sao trong tệp SQLite: đo trên
    máy thật là 206 đoạn quy định đang dùng nhưng 2472 hàng trên 12 segment, tệp 19 MB.
    Không sai kết quả — truy vấn chỉ đọc segment còn sống — nhưng tệp phình mãi và
    mọi phép đếm chạy trực tiếp trên SQLite đều ra con số gấp hơn mười lần sự thật.

    An toàn theo ĐÚNG định nghĩa mồ côi mà `prune_orphan_segments` đang dùng: chỉ
    đụng hàng có `segment_id` KHÔNG còn trong bảng `segments`. Lỗi (tệp đang bị khóa,
    lược đồ Chroma đổi) thì bỏ qua — dọn rác không được phép làm hỏng lượt seed."""
    db = os.path.join(settings.chroma_persist_dir, "chroma.sqlite3")
    if not os.path.exists(db):
        return 0
    try:
        con = sqlite3.connect(db, timeout=5.0)
        try:
            # CHỐT AN TOÀN: bảng `segments` rỗng thì MỌI hàng đều trông như mồ côi và
            # lệnh dưới sẽ xóa sạch kho. Trạng thái đó chỉ xảy ra khi có gì đó đã sai
            # (seed hỏng giữa chừng, tệp cụt) — lúc đó không dọn là đúng.
            if not con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]:
                return 0
            orphan = [r[0] for r in con.execute(
                "SELECT id FROM embeddings WHERE segment_id NOT IN (SELECT id FROM segments)")]
            if orphan:
                marks = ",".join("?" * len(orphan))
                # FTS5 lưu theo rowid = id của hàng metadata; xóa trước khi xóa nguồn.
                con.execute(f"DELETE FROM embedding_fulltext_search WHERE rowid IN ({marks})",
                            orphan)
                con.execute(f"DELETE FROM embedding_metadata WHERE id IN ({marks})", orphan)
                con.execute(f"DELETE FROM embeddings WHERE id IN ({marks})", orphan)
            con.execute("DELETE FROM max_seq_id WHERE segment_id NOT IN (SELECT id FROM segments)")
            con.commit()
            if orphan:
                con.execute("VACUUM")     # trả lại phần đĩa vừa giải phóng
            return len(orphan)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — dọn rác hỏng không được làm hỏng seed
        print(f"[seed] bỏ qua dọn hàng mồ côi trong chroma.sqlite3: {exc!r}")
        return 0


def upsert_chunks(
    ids: list[str],
    texts: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict[str, Any]],
) -> None:
    """Ghi/đè các đoạn quy định vào collection. `upsert` (không phải `add`): id trùng thì
    ĐÈ LÊN, nên nạp lại cùng một văn bản không sinh bản sao (xem cách dựng id ở
    `ingest_markdown_text`). Bốn danh sách phải cùng độ dài và cùng thứ tự."""
    get_collection().upsert(
        ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas,
    )


__all__ = [
    "get_client", "get_collection", "prune_orphan_rows", "prune_orphan_segments",
    "reset_collection", "upsert_chunks",
]
