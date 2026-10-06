"""NGHIỆP VỤ ĐỌC HỒ SƠ (textlayer) — đọc LỚP VĂN BẢN có sẵn trong PDF, trước khi nghĩ tới OCR.

Vì sao tầng này tồn tại: nhiều hồ sơ tới tay hệ thống KHÔNG phải bản quét thuần. Chúng
đã đi qua một công cụ ghép/nén PDF nào đó và mang sẵn một lớp văn bản. Đọc lớp đó mất
**6 mili-giây một trang**; OCR cùng trang đó mất **45 giây**. Trên một hồ sơ 160 trang,
khác biệt là 2 giờ so với 20 phút.

Lợi về CHẤT LƯỢNG còn lớn hơn lợi về tốc độ. Lớp văn bản gốc giữ nguyên dấu tiếng Việt
("BỘ LAO ĐỘNG – THƯƠNG BINH VÀ XÃ HỘI"), trong khi cùng trang đó OCR sẽ rụng nguyên âm
mang dấu rồi phải nhờ tầng khôi phục dấu — mắt xích yếu nhất của cả chuỗi. Bỏ được một
tầng rủi ro thì đáng hơn bỏ được vài phút.

NHƯNG KHÔNG ĐƯỢC TIN MÙ. Lớp văn bản có thể chính là kết quả của một lượt OCR KÉM do
công cụ khác nhúng sẵn vào tệp. Ví dụ thật, cùng một tệp:

    trang 2   "BỘ LAO ĐỘNG – THƯƠNG BINH VÀ XÃ HỘI"        <- dùng được
    trang 40  "ACIiNI shall i,nnrediatcly causc thc rcp.triatiol"  <- rác

Tin trang 40 là trích ra giá trị sai, mà sai thì nguy hơn chậm. Nên mỗi trang được CHẤM
ĐIỂM (`junk_ratio`) và chỉ trang đủ sạch mới được dùng; phần còn lại trả về cho OCR.

Bố cục:
  1) `page_lines`   — rút từng trang thành DÒNG (cùng dạng dữ liệu với đường đọc ảnh);
  2) `junk_ratio`   — chấm điểm rác;
  3) `plan`         — quyết định trang nào đọc thẳng, trang nào phải OCR.
"""
from __future__ import annotations

from typing import Any

from app.core import settings

# Dòng cùng một hàng: đỉnh lệch nhau dưới `_ROW_TOL` lần chiều cao chữ.
_ROW_TOL = 0.6
# Khoảng trống ngang vượt `_COL_GAP` lần bề ngang trang thì coi là sang CỘT khác —
# biểu mẫu hai cột (quốc hiệu bên phải, tên cơ quan bên trái) nếu gộp làm một dòng sẽ
# ra "THƯƠNG BINH VÀCỘNG HOÀ XÃ HỘI".
_COL_GAP = 0.03
_PT_PER_INCH = 72.0


def junk_ratio(text: str) -> float | None:
    """Tỉ lệ từ có dấu hiệu RÁC. `None` khi trang quá ít chữ để kết luận.

    Hai dấu hiệu, đều lấy từ cách một lượt OCR kém làm hỏng chữ:
      · ký tự không phải chữ/số nằm GIỮA hai chữ cái — `i,nnrediatcly`, `rcp.triatiol`;
      · chữ HOA đứng ngay sau chữ thường trong cùng một từ — `ACIiNI`, `thc rcp`.

    Cố ý KHÔNG dùng từ điển: hồ sơ trộn tiếng Việt, tiếng Anh và tên riêng nước ngoài,
    nên "từ này không có trong từ điển" không phân biệt được rác với tên tàu. Hai dấu
    hiệu trên thì chỉ sinh ra từ lỗi nhận dạng, không sinh ra từ văn bản gõ tay.

    Dùng `str.isupper()`/`islower()` chứ KHÔNG dùng khoảng mã Unicode: khoảng `à-ỹ`
    nuốt luôn cả chữ HOA tiếng Việt (`Đ` U+0110, `Ộ` U+1ED8 đều nằm trong đó), nên mọi
    trang tiếng Việt viết hoa sẽ bị chấm là rác.

    Chỉ chấm trên từ có CHỮ CÁI LATIN. Cả hai dấu hiệu đều là lỗi của nhận dạng chữ
    Latin; đem áp cho một trang tiếng Nhật (`第 5 項 上記第 1項乃至`) thì trang đó bị
    chấm 0,55 và bị vứt đi oan — trong khi lớp văn bản của nó lại chính xác hơn hẳn thứ
    mà một model Latin OCR lại được."""
    words = [w for w in (text or "").split()
             if len(w) >= 2 and sum(c.isascii() and c.isalpha() for c in w) >= 2]
    if len(words) < 20:
        return None
    bad = 0
    for w in words:
        core = w.strip(".,;:()[]{}\"'-–—/*|")
        if len(core) < 2:
            continue
        if any(not core[i].isalnum() and core[i - 1].isalpha() and core[i + 1].isalpha()
               for i in range(1, len(core) - 1)) or \
           any(core[i - 1].islower() and core[i].isupper() for i in range(1, len(core))):
            bad += 1
    return round(bad / len(words), 3)


def _page_lines(page) -> list[dict[str, Any]]:
    """Một trang PDF -> danh sách dòng `{text, conf}` cùng dạng với đường đọc ảnh.

    Tọa độ ô chữ (điểm PDF) chỉ dùng để gom ô thành dòng và tách cột ngay trong hàm này.

    Text của mỗi dòng lấy bằng MỘT lần `get_text_bounded` trên khung bao của dòng, chứ
    không nối chuỗi của từng ô chữ: các ô chữ chồng lấn nhau nên nối tay sẽ nhân đôi ký
    tự ("Tự ự do", "Hà N Nội")."""
    tp = page.get_textpage()
    w_pt, _h_pt = page.get_size()

    boxes: list[tuple[float, float, float, float]] = []
    for i in range(tp.count_rects()):
        rect = tp.get_rect(i)
        if tp.get_text_bounded(*rect).strip():
            boxes.append(rect)
    if not boxes:
        return []

    boxes.sort(key=lambda r: (-r[3], r[0]))          # trên xuống dưới, trái sang phải
    bands: list[list[tuple[float, float, float, float]]] = [[boxes[0]]]
    for rect in boxes[1:]:
        head = bands[-1][0]
        if abs(rect[3] - head[3]) <= _ROW_TOL * max(1e-6, head[3] - head[1]):
            bands[-1].append(rect)
        else:
            bands.append([rect])

    lines: list[dict[str, Any]] = []
    for band in bands:
        band.sort(key=lambda r: r[0])
        seg = [band[0]]
        for rect in band[1:]:
            if rect[0] - max(s[2] for s in seg) > w_pt * _COL_GAP:
                lines.append(_line(tp, seg))
                seg = [rect]
            else:
                seg.append(rect)
        lines.append(_line(tp, seg))
    return [ln for ln in lines if ln["text"]]


def _line(tp, seg) -> dict[str, Any]:
    left = min(r[0] for r in seg)
    right = max(r[2] for r in seg)
    bottom = min(r[1] for r in seg)
    top = max(r[3] for r in seg)
    text = " ".join(tp.get_text_bounded(left, bottom, right, top).split())
    return {"text": text, "conf": 1.0}


def read_pages(data: bytes) -> list[dict[str, Any]]:
    """Rút lớp văn bản của MỌI trang. Lỗi/thiếu thư viện -> danh sách rỗng (rơi về OCR).

    Mỗi phần tử: `{index, lines, chars, junk, meta}`."""
    try:
        import pypdfium2  # noqa: F401,PLC0415 — chỉ kiểm tra có thư viện; thiếu thì rơi về OCR
    except Exception as exc:  # noqa: BLE001
        print(f"[textlayer] không nạp được pypdfium2 ({exc!r}) — dùng OCR cho mọi trang.")
        return []

    from app.domain.documents.ocr.layout import open_pdf  # noqa: PLC0415 — trần số trang

    dpi = float(settings.ocr_dpi)
    out: list[dict[str, Any]] = []
    pdf = open_pdf(data)
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                lines = _page_lines(page)
            except Exception:  # noqa: BLE001 — một trang hỏng không được chặn cả tệp
                lines = []
            text = "\n".join(ln["text"] for ln in lines)
            w_pt, h_pt = page.get_size()
            out.append({
                "index": i,
                "lines": lines,
                "chars": len("".join(text.split())),
                "junk": junk_ratio(text),
                "meta": {"rotate_k": 0,
                         "h": int(h_pt * dpi / _PT_PER_INCH),
                         "w": int(w_pt * dpi / _PT_PER_INCH)},
            })
    finally:
        pdf.close()
    return out


def trusted(page: dict[str, Any]) -> bool:
    """Trang này có được đọc thẳng từ lớp văn bản không.

    Số ký tự là điều kiện CẦN: dưới ngưỡng thì đấy là trang ảnh chỉ dính vài chữ ở
    tiêu đề, đọc thẳng chẳng lợi gì mà bỏ sót nội dung ảnh thì có thật.

    Điểm rác `None` nghĩa là KHÔNG ĐỦ TỪ LATIN ĐỂ CHẤM, và trang như vậy được TIN. Đó
    là trang chữ Nhật hoặc chữ Trung — phép chấm rác chỉ bắt được lỗi nhận dạng chữ
    Latin nên nó không có ý kiến gì ở đây, mà "không có bằng chứng hỏng" thì không
    được đọc thành "có bằng chứng hỏng". Quan trọng hơn: đem model Latin OCR lại một
    trang tiếng Nhật chắc chắn tệ hơn chính lớp văn bản đang có."""
    if not settings.ocr_text_layer:
        return False
    if page["chars"] < int(settings.ocr_text_layer_min_chars):
        return False
    junk = page["junk"]
    return junk is None or junk <= float(settings.ocr_text_layer_max_junk)


def plan(data: bytes) -> dict[str, Any]:
    """Chia trang thành hai nhóm: đọc thẳng và phải OCR.

    Trả `{"pages": [...], "from_text": [chỉ số], "need_ocr": [chỉ số], "total": n}`.
    Không đọc được lớp văn bản -> `pages` rỗng và `need_ocr` cũng rỗng: nơi gọi hiểu là
    "không biết gì" và quay về đường OCR toàn bộ như trước."""
    pages = read_pages(data) if settings.ocr_text_layer else []
    if not pages:
        return {"pages": [], "from_text": [], "need_ocr": [], "total": 0}
    from_text = [p["index"] for p in pages if trusted(p)]
    need_ocr = [p["index"] for p in pages if not trusted(p)]
    return {"pages": pages, "from_text": from_text, "need_ocr": need_ocr,
            "total": len(pages)}


__all__ = ["junk_ratio", "plan", "read_pages", "trusted"]
