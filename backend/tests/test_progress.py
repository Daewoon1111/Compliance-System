"""Test TIẾN ĐỘ (progress) — registry in-memory + luồng SSE.

Đây là thứ DUY NHẤT người dùng nhìn thấy trong 2-3 phút OCR chạy. Hỏng ở đây thì
thanh tiến độ đứng im hoặc quay mãi, còn hồ sơ vẫn chạy bình thường phía sau — loại
lỗi không có test nào bắt được và cũng không có log nào kêu.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app import progress as pg


@pytest.fixture(autouse=True)
def _clean_registry():
    """Registry là biến MODULE dùng chung — test rò dữ liệu sang nhau thì kết quả
    phụ thuộc thứ tự chạy."""
    pg._PROGRESS.clear()
    yield
    pg._PROGRESS.clear()


def test_ghi_moc_va_giu_thong_tin_kem():
    pg.progress_update("p1", "ocr", file=2, files=5, page=3, note="a.pdf")
    rec = pg._PROGRESS["p1"]
    assert rec["stage"] == "ocr" and rec["file"] == 2 and rec["note"] == "a.pdf"
    assert isinstance(rec["ts"], float)


def test_pid_rong_bi_bo_qua():
    """Đường validate truyền `pid=""` khi client không gửi progress_id. Ghi vào khóa
    rỗng thì mọi phiên không có pid dùng CHUNG một bản ghi và ghi đè lẫn nhau."""
    pg.progress_update("", "ocr")
    assert pg._PROGRESS == {}


def test_moc_moi_ghi_de_moc_cu_cua_cung_pid():
    pg.progress_update("p1", "ocr", page=1)
    pg.progress_update("p1", "extract", note="x")
    assert pg._PROGRESS["p1"]["stage"] == "extract"
    assert "page" not in pg._PROGRESS["p1"], "mốc mới phải THAY, không trộn với mốc cũ"


def test_don_ban_ghi_qua_han(monkeypatch):
    """Phiên bỏ dở giữa chừng không bao giờ tới `done` — không dọn thì registry phình
    theo số lần người dùng đóng tab."""
    pg.progress_update("cu", "ocr")
    pg._PROGRESS["cu"]["ts"] = time.time() - pg._TTL_SECONDS - 1
    pg.progress_update("moi", "ocr")
    assert "cu" not in pg._PROGRESS and "moi" in pg._PROGRESS


# ---------------------------------------------------------------------------
# Luồng SSE
# ---------------------------------------------------------------------------
def _drain(pid: str, limit: int = 10) -> list[str]:
    """Đọc luồng SSE tới khi nó tự đóng. Chạy qua `asyncio.run` chứ không dùng
    `pytest-asyncio`: dự án ghim cứng phụ thuộc, thêm một gói chỉ để chạy bốn test
    là cái giá không đáng."""
    async def _go() -> list[str]:
        out: list[str] = []
        async for chunk in pg._sse_stream(pid):
            out.append(chunk)
            if len(out) >= limit:
                break
        return out

    return asyncio.run(_go())


def test_sse_dong_khi_xong_va_khong_lap_moc_trung(monkeypatch):
    """Chỉ đẩy khi trạng thái ĐỔI, và tự đóng ở `done` — không đóng thì kết nối treo
    tới hết `_STREAM_TIMEOUT` (30 phút) cho mỗi hồ sơ đã chạy xong."""
    monkeypatch.setattr(pg, "_POLL_SECONDS", 0)
    pg.progress_update("p1", "ocr", page=1)
    pg._PROGRESS["p1"] = {**pg._PROGRESS["p1"], "stage": "done"}
    chunks = _drain("p1")
    assert len(chunks) == 1
    assert chunks[0].startswith("data: ") and chunks[0].endswith("\n\n")
    assert '"stage": "done"' in chunks[0]


def test_sse_dong_khi_loi(monkeypatch):
    monkeypatch.setattr(pg, "_POLL_SECONDS", 0)
    pg.progress_update("p1", "error", note="Ollama không phản hồi")
    chunks = _drain("p1")
    assert len(chunks) == 1 and "Ollama" in chunks[0]


def test_sse_tu_dong_khi_khong_co_gi_thay_doi(monkeypatch):
    """Không có bản ghi nào -> vòng lặp phải HẾT HẠN chứ không quay vô tận."""
    monkeypatch.setattr(pg, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(pg, "_STREAM_TIMEOUT", 0.005)
    assert _drain("khong-ton-tai") == []


def test_sse_giu_nguyen_dau_tieng_viet(monkeypatch):
    """`ensure_ascii=False`: mất dòng này thì mọi thông báo tiếng Việt tới trình duyệt
    thành `\\u1ea3` và người dùng đọc được một dãy mã."""
    monkeypatch.setattr(pg, "_POLL_SECONDS", 0)
    pg.progress_update("p1", "done", note="Đã kiểm tra xong hồ sơ")
    assert "Đã kiểm tra xong hồ sơ" in _drain("p1")[0]
