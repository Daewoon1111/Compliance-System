"""KHO QUY ĐỊNH (pdf2md) — chuyển văn bản quy định dạng PDF sang Markdown để nạp vào kho.

Hai loại PDF, hai đường đọc:
  · PDF CÓ LỚP CHỮ (xuất từ Word, tải từ cổng văn bản pháp luật): đọc thẳng lớp chữ bằng
    pypdfium2 — cùng bộ đọc `textlayer` mà hệ thống dùng cho hồ sơ, nhanh và giữ đúng dấu;
  · PDF QUÉT (ảnh chụp từng trang): không có chữ để đọc thẳng -> đọc ảnh bằng Vintern (OCR)
    khi bật `ocr=True` / `--ocr`. Không bật thì trang đó được BÁO RÕ là bỏ qua, chứ không
    lặng lẽ biến mất khỏi văn bản.

Trang có lớp chữ nhưng là RÁC (lớp OCR kém do công cụ khác nhúng sẵn) được nhận ra bằng
cùng phép chấm `junk_ratio` của `textlayer` và xử lý như trang quét.

Dựng Markdown giống `docx2md` để bộ cắt đoạn và trích dẫn hiểu như nhau:
  · dòng "Phần / Chương / Mục / Điều" -> tiêu đề Markdown;
  · các dòng hiển thị liền nhau được NỐI lại thành đoạn; đoạn mới khi gặp tiêu đề, khoản
    ("1.", "2."), điểm ("a)", "b)"), gạch đầu dòng, hoặc dòng trước kết thúc câu;
  · dòng chỉ có số trang ("1", "- 2 -", "Trang 3/10") bị bỏ.

Dùng:
    npm run pdf2md -- "D:/Quy dinh/Thong tu 01.pdf"            # ghi .md cạnh file gốc
    npm run pdf2md -- quy_dinh.pdf --rules                    # ghi thẳng vào app/rules/
    npm run pdf2md -- ban_quet.pdf --ocr                      # PDF quét: đọc ảnh bằng Vintern
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from app.core import settings
from app.domain.regulations.docx2md import _STRUCT, _safe_stem

# Đầu khoản / điểm / gạch đầu dòng -> luôn mở đoạn mới.
_ITEM = re.compile(r"^(\d{1,3}[.)]|[a-zđ]\)|[-–•+*]\s)", re.I)
# Dòng chỉ là số trang.
_PAGE_NO = re.compile(r"^(?:[-–]\s*)?(?:trang\s+)?\d{1,4}(?:\s*/\s*\d{1,4})?(?:\s*[-–])?$", re.I)
# Dòng kết thúc câu / ý -> dòng sau là đoạn mới.
_END = re.compile(r"[.:;!?]$|[.:;!?][\"”)]$")


def _usable(page: dict) -> bool:
    """Trang có lớp chữ đủ nhiều và đủ sạch để dùng thẳng."""
    if page["chars"] < int(settings.ocr_text_layer_min_chars):
        return False
    junk = page["junk"]
    return junk is None or junk <= float(settings.ocr_text_layer_max_junk)


def _heading(line: str) -> int:
    for rx, lv in _STRUCT:
        if rx.match(line):
            return lv
    return 0


def lines_to_markdown(lines: list[str], title: str = "") -> str:
    """Các dòng hiển thị (theo thứ tự đọc) -> Markdown có tiêu đề và đoạn."""
    blocks: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(" ".join(para))
            para.clear()

    for raw in lines:
        line = " ".join((raw or "").split())
        if not line or _PAGE_NO.match(line):
            continue
        level = _heading(line)
        if level:
            flush()
            blocks.append("#" * level + " " + line)
            continue
        # Tên Điều thường xuống dòng: "Điều 5. Quyền và nghĩa vụ" / "của các bên" — dòng
        # tiếp theo KHÔNG phải khoản và tiêu đề chưa kết thúc câu thì nối vào tiêu đề.
        if not para and blocks and blocks[-1].startswith("#") and not _ITEM.match(line) \
                and not _END.search(blocks[-1]) and len(blocks[-1]) < 120 and line[:1].islower():
            blocks[-1] += " " + line
            continue
        if _ITEM.match(line) or (para and _END.search(para[-1])):
            flush()
        para.append(line)
    flush()
    if title and not (blocks and blocks[0].startswith("# ")):
        blocks.insert(0, f"# {title.strip()}")
    return "\n\n".join(blocks).strip() + "\n"


def _cached_lines(cache_dir: Path | None, idx: int) -> list[str] | None:
    if cache_dir is None:
        return None
    try:
        return json.loads((cache_dir / f"{idx}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cache_lines(cache_dir: Path | None, idx: int, lines: list[str]) -> None:
    if cache_dir is None:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{idx}.json").write_text(json.dumps(lines, ensure_ascii=False),
                                               encoding="utf-8")
    except OSError:
        pass  # bộ nhớ đệm chỉ để tăng tốc lần chạy lại — ghi hỏng không được chặn việc chính


def pdf_to_markdown(data: bytes, title: str = "", ocr: bool = False,
                    on_page=None, cache_dir: Path | None = None) -> tuple[str, dict]:
    """Nội dung một tệp PDF (bytes) -> (Markdown, thống kê).

    Thống kê: `{pages, from_text, ocred, skipped}` — `skipped` là các trang (đếm từ 1) không
    có chữ dùng được mà cũng không được OCR. `on_page(i, n)` (tùy chọn) báo tiến độ OCR.
    Không rút được chữ nào -> `ValueError` (thay vì trả một tệp .md rỗng).

    `cache_dir`: lưu chữ đọc được của TỪNG TRANG QUÉT. Bản quét 200 trang mất hàng giờ
    đọc ảnh trên CPU; lượt nạp bị ngắt giữa chừng (tắt máy, đóng ứng dụng) mà không có bộ
    nhớ này thì lần nạp lại phải đọc lại từ trang 1."""
    from app.domain.documents import textlayer  # noqa: PLC0415

    try:
        pages = textlayer.read_pages(data)
    except Exception as exc:  # noqa: BLE001 — PdfiumError, DocumentTooLargeError…
        raise ValueError(f"Không đọc được tệp PDF: {exc}") from exc
    if not pages:
        raise ValueError("Không đọc được tệp PDF (tệp hỏng hoặc có mật khẩu).")
    texts: dict[int, list[str]] = {}
    scan = []
    for p in pages:
        if _usable(p):
            texts[p["index"]] = [ln["text"] for ln in p["lines"]]
        else:
            scan.append(p["index"])
    ocred: list[int] = []
    if scan and ocr:
        from app.domain.documents.ocr import ocr_image_lines, render_pages  # noqa: PLC0415

        todo = []
        for idx in scan:
            if (hit := _cached_lines(cache_dir, idx)) is not None:
                texts[idx] = hit
                ocred.append(idx)
            else:
                todo.append(idx)
        if on_page and ocred:
            on_page(len(ocred), len(scan))
        for idx, img in render_pages(data, todo):
            texts[idx] = [ln["text"] for ln in ocr_image_lines(img)]
            _cache_lines(cache_dir, idx, texts[idx])
            ocred.append(idx)
            if on_page:
                on_page(len(ocred), len(scan))
    skipped = [i + 1 for i in scan if i not in texts]
    lines = [ln for i in sorted(texts) for ln in texts[i]]
    if not any(line.strip() for line in lines):
        raise ValueError(
            "Tệp PDF là bản chụp/quét, không có chữ để đọc thẳng — hãy bật 'Đọc chữ trong "
            "ảnh' khi nạp bộ quy định (dòng lệnh: thêm --ocr)." if not ocr else
            "Không đọc được chữ nào từ tệp PDF.")
    md = lines_to_markdown(lines, title)
    stats = {"pages": len(pages), "from_text": len(pages) - len(scan),
             "ocred": len(ocred), "skipped": skipped}
    return md, stats


def main(argv: list[str] | None = None) -> int:
    from app.domain.regulations.corpus import RULES_DIR  # noqa: PLC0415

    ap = argparse.ArgumentParser(prog="pdf2md", description="Chuyển .pdf sang .md cho kho quy định.")
    ap.add_argument("inputs", nargs="+", help="Tệp .pdf (một hoặc nhiều)")
    ap.add_argument("-o", "--output", help="Tệp .md đầu ra (chỉ khi có MỘT tệp vào)")
    ap.add_argument("--rules", action="store_true",
                    help="Ghi thẳng vào app/rules/ (sau đó chạy npm run seed để nạp vào kho)")
    ap.add_argument("--title", default="", help="Tên văn bản (mặc định: tên tệp)")
    ap.add_argument("--ocr", action="store_true",
                    help="Đọc ảnh bằng Vintern cho trang không có lớp chữ (PDF quét) — chậm")
    args = ap.parse_args(argv)
    if args.output and len(args.inputs) > 1:
        ap.error("-o chỉ dùng khi chuyển MỘT tệp.")

    base = Path(os.environ.get("INIT_CWD") or os.getcwd())
    status = 0
    for raw in args.inputs:
        src = Path(raw) if Path(raw).is_absolute() else base / raw
        if src.suffix.lower() != ".pdf":
            print(f"[pdf2md] BỎ QUA {src.name}: chỉ nhận .pdf.")
            status = 1
            continue
        try:
            md, st = pdf_to_markdown(
                src.read_bytes(), args.title or src.stem.replace("_", " "), ocr=args.ocr,
                on_page=lambda i, n: print(f"[pdf2md] OCR trang {i}/{n}…"))
        except Exception as exc:  # noqa: BLE001 — một tệp hỏng không chặn các tệp còn lại
            print(f"[pdf2md] LỖI {src.name}: {exc}")
            status = 1
            continue
        if args.output:
            out = Path(args.output) if Path(args.output).is_absolute() else base / args.output
        elif args.rules:
            out = RULES_DIR / f"{_safe_stem(src.stem)}.md"
        else:
            out = src.with_suffix(".md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        note = f" — BỎ QUA trang quét {st['skipped']} (thêm --ocr để đọc)" if st["skipped"] else ""
        print(f"[pdf2md] {src.name} -> {out} ({st['from_text']} trang lớp chữ, "
              f"{st['ocred']} trang OCR){note}")
    return status


if __name__ == "__main__":
    sys.exit(main())
