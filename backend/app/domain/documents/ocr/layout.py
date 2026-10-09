"""ĐỌC HỒ SƠ · ocr.layout — trang PDF -> ảnh -> DÒNG CHỮ (qua Vintern).

  - Render bằng pypdfium2 (tự chứa, không cần DLL hệ thống), từng trang một: ảnh
    200 DPI ~25 MB/trang, không giữ cả tệp trong RAM.
  - Xóa dấu mộc đỏ đè chữ (numpy thuần) trước khi đọc.
  - Tự xoay trang nằm ngang / lộn ngược: 0°/90° suy từ biên dạng mực (rẻ, không cần
    model), chiều cụ thể và 180° chọn bằng điểm tin cậy của một lượt đọc ảnh nhỏ.
"""
from __future__ import annotations

from typing import Any

from app.core import settings

from . import vintern
from .text import _is_foreign_script_line, _strip_foreign_chars

# Trần điểm ảnh MỘT trang sau render. Kích thước trang do tệp PDF tự khai: một trang khai
# 200 x 200 inch ở 200 DPI là 1,6 tỉ điểm ảnh (~4,8 GB RAM) — một tệp vài KB đủ làm sập máy.
_MAX_PAGE_PIXELS = 40_000_000


class DocumentTooLargeError(ValueError):
    """Tệp PDF vượt trần số trang được phép xử lý."""


def open_pdf(data: bytes):
    """PdfDocument từ bytes; quá `ocr_max_pages` trang -> `DocumentTooLargeError`."""
    import pypdfium2 as pdfium  # noqa: PLC0415

    pdf = pdfium.PdfDocument(data)
    if len(pdf) > int(settings.ocr_max_pages):
        n = len(pdf)
        pdf.close()
        raise DocumentTooLargeError(
            f"Tệp có {n} trang, vượt giới hạn {settings.ocr_max_pages} trang mỗi tệp.")
    return pdf


def render_page(pdf, index: int, dpi: int):
    """Một trang -> ảnh PIL RGB ở `dpi` (tự hạ tỉ lệ nếu vượt trần điểm ảnh)."""
    page = pdf[index]
    w_pt, h_pt = page.get_size()
    scale = dpi / 72.0
    pixels = (w_pt * scale) * (h_pt * scale)
    if pixels > _MAX_PAGE_PIXELS:
        scale *= (_MAX_PAGE_PIXELS / pixels) ** 0.5
    return page.render(scale=scale).to_pil().convert("RGB")


def render_pages(data: bytes, indexes: list[int] | None = None, dpi: int | None = None):
    """YIELD (chỉ số, ảnh) cho các trang cần đọc — mặc định mọi trang, theo thứ tự."""
    pdf = open_pdf(data)
    try:
        wanted = range(len(pdf)) if indexes is None else indexes
        for i in wanted:
            yield i, render_page(pdf, i, int(dpi or settings.ocr_dpi))
    finally:
        pdf.close()


def remove_red_stamp(img):
    """Tô trắng pixel ĐỎ (dấu mộc, triện) — chữ đen bên dưới lộ ra, mô hình đỡ đọc nhầm."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = (r > 130) & (r - g > 60) & (r - b > 50)
    if not mask.any():
        return img
    out = a.astype(np.uint8)
    out[mask] = 255
    return Image.fromarray(out)


def _close_1d(ink, width: int, axis: int):
    """Đóng hình thái học 1 chiều (giãn rồi co) bằng tổng tích lũy: lấp khe < `width`."""
    import numpy as np  # noqa: PLC0415

    def _slide(a, w):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (w // 2, w - 1 - w // 2)
        c = np.cumsum(np.pad(a.astype(np.int32), pad), axis=axis)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
        n = a.shape[axis]
        return np.take(c, np.arange(w, n + w), axis=axis) - np.take(c, np.arange(0, n), axis=axis)

    dil = _slide(ink, width) > 0
    return _slide(dil, width) == width


def _mean_run(mask, axis: int) -> float:
    import numpy as np  # noqa: PLC0415

    m = np.moveaxis(mask, axis, -1).astype(np.int8)
    starts = (np.diff(m, axis=-1, prepend=0) == 1).sum()
    return float(m.sum() / starts) if starts else 0.0


def ink_axis_ratio(img) -> float:
    """Độ dài trung bình vệt mực NGANG chia DỌC, sau khi lấp khe giữa các chữ.

    Lấp khe theo chiều ngang thì các chữ trên một dòng liền thành vệt dài bằng dòng; lấp
    theo chiều dọc thì vệt chỉ cao bằng một dòng chữ. Trang thẳng -> tỉ lệ lớn (> 1),
    trang xoay 90° -> tỉ lệ nhỏ (< 1). Không phụ thuộc bố cục một cột hay biểu mẫu nhiều
    cột, khác với biên dạng tổng mực theo hàng/cột."""
    import numpy as np  # noqa: PLC0415

    g = img.convert("L")
    g.thumbnail((1000, 1000))
    ink = np.asarray(g) < 128
    if ink.sum() < 200:
        return 1.0
    gap = max(3, int(max(ink.shape) * 0.012))
    h = _mean_run(_close_1d(ink, gap, axis=1), axis=1)
    v = _mean_run(_close_1d(ink, gap, axis=0), axis=0)
    return h / max(v, 1e-6)


# Trang TRẮNG: tỉ lệ điểm mực dưới ngưỡng này (đo trên ảnh thu nhỏ ~1000 px). Một dòng chữ
# ngắn đã vượt xa ngưỡng; bụi, viền máy quét, số trang lẻ loi thì không.
BLANK_INK_RATIO = 0.0008


def is_blank(img) -> bool:
    """Trang không có chữ (trang trắng, trang ngăn cách của bản quét).

    PHẢI chặn trước khi đưa vào mô hình đọc ảnh: Vintern gặp trang trắng không trả rỗng mà
    BỊA ra chữ ("NGUYÊN VĂN TẤN TẤN…") — đo thật trên một PDF 3 trang trắng. Chữ bịa đi
    thẳng vào hồ sơ hoặc kho quy định, và tốn cả phút đọc mỗi trang cho không gì cả."""
    import numpy as np  # noqa: PLC0415

    g = img.convert("L")
    g.thumbnail((1000, 1000))
    ink = np.asarray(g) < 160
    return float(ink.mean()) < BLANK_INK_RATIO


def _thumb(img, k: int):
    t = img.rotate(90 * k, expand=True) if k else img.copy()
    t.thumbnail((896, 896))
    return t


def choose_rotation(img) -> int:
    """Số lần xoay 90° ngược chiều kim đồng hồ cần áp (0-3)."""
    if not settings.ocr_auto_rotate:
        return 0
    if ink_axis_ratio(img) < 0.7:                  # trang nằm ngang -> chọn 90° hay 270°
        s1 = vintern.quick_confidence(_thumb(img, 1))
        s3 = vintern.quick_confidence(_thumb(img, 3))
        return 1 if s1 >= s3 else 3
    return 0


def ocr_image_lines(img, with_meta: bool = False):
    """Ảnh trang PIL -> [{text, conf}] (xoay + xóa mộc + lọc chữ nước ngoài).

    Trang trắng (sau khi xóa mộc) -> [] ngay, không gọi mô hình (xem `is_blank`)."""
    if settings.ocr_remove_red_stamp:
        img = remove_red_stamp(img)
    if is_blank(img):
        print("[ocr] trang trắng — bỏ qua, không đọc.")
        return ([], {"rotate_k": 0, "h": int(img.size[1]), "w": int(img.size[0])}) if with_meta else []
    k = choose_rotation(img)
    if k:
        img = img.rotate(90 * k, expand=True)
        print(f"[ocr] trang nằm ngang — xoay {90 * k}° trước khi đọc.")
    lines = vintern.transcribe(img)
    # Trang có chữ ngang nhưng đọc rất kém: có thể lộn ngược -> so với bản xoay 180°.
    avg = _avg_conf(lines)
    if settings.ocr_auto_rotate and lines and avg < settings.ocr_rotate_conf_threshold:
        if vintern.quick_confidence(_thumb(img, 2)) > vintern.quick_confidence(_thumb(img, 0)) * 1.05:
            img = img.rotate(180, expand=True)
            k = (k + 2) % 4
            print("[ocr] trang lộn ngược — xoay 180° rồi đọc lại.")
            lines = vintern.transcribe(img)
    out: list[dict[str, Any]] = []
    for ln in lines:
        if _is_foreign_script_line(ln["text"]):
            continue
        cleaned = _strip_foreign_chars(ln["text"])
        if cleaned:
            out.append({"text": cleaned, "conf": ln["conf"]})
    if not with_meta:
        return out
    w, h = img.size
    return out, {"rotate_k": k, "h": int(h), "w": int(w)}


def _avg_conf(lines: list[dict[str, Any]]) -> float:
    conf = [ln["conf"] for ln in lines if any(c.isalpha() for c in ln["text"])]
    return sum(conf) / len(conf) if conf else 0.0


__all__ = ["DocumentTooLargeError", "choose_rotation", "ink_axis_ratio", "is_blank", "ocr_image_lines", "open_pdf",
           "remove_red_stamp", "render_page", "render_pages"]
