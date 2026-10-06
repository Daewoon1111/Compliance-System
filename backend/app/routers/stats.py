"""TẦNG API (stats) — thống kê + nhật ký kiểm tra + kho hồ sơ (tìm kiếm, nhắc hạn)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers.admin import require_admin
from app.store import (
    aggregate_stats,
    clear_audit,
    read_audit,
    search_audit,
    upcoming_expirations,
)

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats")
def get_stats():
    """Số liệu tổng hợp toàn hệ cho trang Thống kê (tổng hợp từ nhật ký kiểm tra)."""
    return aggregate_stats()


@router.get("/audit")
def get_audit(limit: int = 200, q: str = "", market: str = "", verdict: str = ""):
    """Nhật ký kiểm tra + KHO HỒ SƠ: q = tìm toàn văn (bỏ dấu, khớp tên file/thị
    trường/loại hình/session), market + verdict = lọc."""
    limit = max(1, min(limit, 2000))   # chặn ?limit=10**9 kéo cả nhật ký vào RAM
    if q or market or verdict:
        return {"records": search_audit(q=q, market=market, verdict=verdict, limit=limit)}
    return {"records": read_audit(limit)}


@router.get("/reminders")
def get_reminders(days: int = 90):
    """HẬU KIỂM: hợp đồng sắp hết hạn trong `days` ngày (signed_date + thời hạn)."""
    days = max(0, min(days, 3650))
    return {"days": days, "expiring": upcoming_expirations(days)}


@router.delete("/audit", dependencies=[Depends(require_admin)])
def delete_audit():
    """XÓA TOÀN BỘ nhật ký kiểm tra.

    Không hoàn tác được và xóa luôn nguồn của cả trang Thống kê lẫn trang Lịch sử,
    nên bắt buộc có mã quản trị: endpoint mở thì bất kỳ trang web nào cũng gửi được
    DELETE tới localhost:8000."""
    return clear_audit()
