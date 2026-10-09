"""TẦNG API (stats) — thống kê + nhật ký kiểm tra + kho hồ sơ (tìm kiếm, lọc)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers.admin import require_admin
from app.store import aggregate_stats, clear_audit, read_audit, search_audit

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats")
def get_stats():
    """Số liệu tổng hợp toàn hệ cho trang Thống kê (tổng hợp từ nhật ký kiểm tra)."""
    return aggregate_stats()


@router.get("/audit")
def get_audit(limit: int = 200, q: str = "", field_set: str = "", verdict: str = ""):
    """Nhật ký kiểm tra + KHO HỒ SƠ: q = tìm toàn văn (bỏ dấu, khớp tên file/bộ trường/
    session), field_set + verdict = lọc."""
    limit = max(1, min(limit, 2000))   # chặn ?limit=10**9 kéo cả nhật ký vào RAM
    if q or field_set or verdict:
        return {"records": search_audit(q=q, field_set=field_set, verdict=verdict, limit=limit)}
    return {"records": read_audit(limit)}


@router.delete("/audit", dependencies=[Depends(require_admin)])
def delete_audit():
    """XÓA TOÀN BỘ nhật ký kiểm tra.

    Không hoàn tác được và xóa luôn nguồn của cả trang Thống kê lẫn trang Lịch sử,
    nên bắt buộc có mã quản trị: endpoint mở thì bất kỳ trang web nào cũng gửi được
    DELETE tới máy chủ cục bộ."""
    return clear_audit()
