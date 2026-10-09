"""TẦNG API (export) — XUẤT BÁO CÁO PDF của một phiên đã kiểm tra.

fpdf2 + font TTF hệ thống có dấu tiếng Việt. Thiếu thư viện hoặc thiếu font -> 501
kèm ĐÚNG câu lệnh cần chạy, thay vì lỗi 500 không nói gì.

Là router DUY NHẤT phụ thuộc fpdf2 + font của máy chủ, nên lỗi môi trường của việc
xuất file không kéo theo phần còn lại của API.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.store import get_paths, read_json

router = APIRouter(prefix="/api/v1", tags=["export"])

_VERDICT_VI = {
    "PASS": "Hợp lệ", "FAIL": "Không hợp lệ", "NEEDS_SUPPLEMENT": "Cần bổ sung",
    "DECLARATION": "Đã khai báo", "N/A": "Không có",
}


def _pdf_val(v) -> str:
    """Giá trị trường -> chuỗi đọc được (không in JSON thô): tiền {amount,...} -> '550 USD'."""
    if v is None or v == "":
        return ""
    if isinstance(v, dict):
        raw = v.get("raw")
        if isinstance(raw, str) and raw.strip():
            return raw
        if v.get("amount") not in (None, ""):
            money = " ".join(str(x) for x in (v.get("amount"), v.get("currency")) if x)
            return f"{money}/{v['period']}" if v.get("period") else money
        return ""
    return str(v)


def _pdf_fonts() -> tuple[str, str] | None:
    """Tìm font TTF hệ thống có đủ dấu tiếng Việt (thường + đậm)."""
    for reg, bold in (
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        (r"C:\Windows\Fonts\times.ttf", r"C:\Windows\Fonts\timesbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ):
        if os.path.exists(reg):
            return reg, (bold if os.path.exists(bold) else reg)
    return None


@router.get("/sessions/{session_id}/export.pdf")
def export_pdf(session_id: str):
    """Xuất báo cáo kiểm tra của một phiên ra PDF.

    Trả 501 (chứ không phải 500) khi thiếu fpdf2 hoặc thiếu font có tiếng Việt: đó là
    'máy chưa cài đủ', không phải 'hệ thống hỏng' — và câu trả lời kèm luôn lệnh cài."""
    paths = get_paths(session_id)
    if not os.path.exists(paths.final_report_json):
        raise HTTPException(status_code=404, detail="Chưa có báo cáo cho phiên này")
    report = read_json(paths.final_report_json)
    try:
        from fpdf import FPDF  # noqa: PLC0415
    except Exception as exc:
        raise HTTPException(status_code=501, detail=(
            "Chức năng xuất PDF chưa sẵn sàng trên máy này. "
            "Cài thư viện bằng lệnh: pip install fpdf2")) from exc
    fonts = _pdf_fonts()
    if not fonts:
        raise HTTPException(status_code=501,
                            detail="Không tìm thấy font hệ thống có tiếng Việt để xuất PDF.")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_font("VN", "", fonts[0])
    pdf.add_font("VN", "B", fonts[1])
    pdf.add_page()
    pdf.set_font("VN", "B", 15)
    pdf.cell(0, 10, "BÁO CÁO KIỂM TRA HỒ SƠ", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("VN", "", 11)
    files = ", ".join(report.get("source_files")
                      or [str(d.get("source_file") or "") for d in report.get("documents", [])])
    for k, v in (
        ("Loại hồ sơ (bộ trường)", report.get("field_set_name", "")),
        ("Tài liệu", files),
        ("Ngày ký", report.get("signed_date", "")),
        ("Kết luận chung", _VERDICT_VI.get(report.get("overall_verdict", ""),
                                           report.get("overall_verdict", ""))),
        ("Thời gian kiểm tra", report.get("checked_at", "")),
    ):
        pdf.multi_cell(0, 7, f"{k}: {v or '—'}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("VN", "B", 12)
    pdf.cell(0, 8, "Chi tiết kiểm tra theo trường", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("VN", "", 9)
    with pdf.table(col_widths=(45, 45, 28, 72)) as table:
        head = table.row()
        for h in ("Trường thông tin", "Giá trị", "Kết quả", "Lý do"):
            head.cell(h)
        for d in report.get("documents", []):
            for c in d.get("checks", []):
                row = table.row()
                row.cell(str(c.get("title", "")))
                row.cell(_pdf_val(c.get("field_value")))
                row.cell(_VERDICT_VI.get(c.get("verdict", ""), c.get("verdict", "")))
                row.cell(str(c.get("reason", "")))

    return Response(
        content=bytes(pdf.output()),
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="bao_cao_{session_id[:8]}.pdf"'},
    )
