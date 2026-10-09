"""KHO QUY ĐỊNH (docx2md) — chuyển văn bản Word (.docx) sang Markdown để nạp vào kho.

Kho quy định chỉ đọc Markdown (`app/rules/*.md`), trong khi văn bản quy định thường được
phát hành dạng Word. Mô-đun này chuyển .docx -> .md CHỈ bằng thư viện chuẩn (zipfile +
xml): .docx là một gói ZIP chứa `word/document.xml`, nên không cần cài thêm gì.

Giữ lại những gì ảnh hưởng tới việc cắt đoạn và trích dẫn:
  · đoạn văn -> cách nhau một dòng trống (bộ cắt đoạn `chunk_markdown` dựa vào đó);
  · tiêu đề (style Heading/Title) và các dòng "Chương / Mục / Điều" -> tiêu đề Markdown;
  · danh sách đánh số tự động (khoản 1., 2., điểm a), b)…) -> dựng lại đúng số;
  · bảng -> bảng Markdown; chữ đậm -> **đậm**.
Bỏ qua: ảnh, chú thích cuối trang, đầu/chân trang, định dạng màu/cỡ chữ.

Dùng:
    npm run docx2md -- "D:/Quy dinh/Nghi dinh 01.docx"            # ghi .md cạnh file gốc
    npm run docx2md -- quy_dinh.docx --rules                     # ghi thẳng vào app/rules/
    npm run docx2md -- quy_dinh.docx -o out.md --title "Tên văn bản"
Tệp .doc (Word 97-2003) phải lưu lại thành .docx trước (Word: File > Save As > .docx).
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Dòng mở đầu cấu trúc văn bản quy phạm -> cấp tiêu đề Markdown.
_STRUCT = [
    (re.compile(r"^(phần|phan)\s+(thứ\s+)?[\wIVXLC]+\b", re.I), 2),
    (re.compile(r"^(chương|chuong)\s+[IVXLC\d]+\b", re.I), 2),
    (re.compile(r"^(mục|muc)\s+\d+\b", re.I), 3),
    (re.compile(r"^(điều|dieu)\s+\d+[a-zđ]?\s*[.:]", re.I), 3),
]


def _attr(el: ET.Element | None, name: str) -> str:
    return "" if el is None else (el.get(_W + name) or "")


def _on(el: ET.Element | None) -> bool:
    """Thuộc tính bật/tắt kiểu <w:b/> hoặc <w:b w:val="0"/>."""
    return el is not None and _attr(el, "val").lower() not in ("0", "false", "off")


# ── Danh sách đánh số ────────────────────────────────────────────────────────
def _roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + s, n - v
    return out


def _fmt_num(n: int, fmt: str) -> str:
    if fmt == "lowerLetter":
        return chr(ord("a") + (n - 1) % 26)
    if fmt == "upperLetter":
        return chr(ord("A") + (n - 1) % 26)
    if fmt == "lowerRoman":
        return _roman(n).lower()
    if fmt == "upperRoman":
        return _roman(n)
    return str(n)


class _Numbering:
    """Dựng lại số thứ tự của danh sách tự động (numbering.xml)."""

    def __init__(self, xml: bytes | None) -> None:
        self.levels: dict[str, dict[int, tuple[str, str, int]]] = {}   # abstractId -> lvl
        self.num_to_abs: dict[str, str] = {}
        self.counters: dict[str, list[int]] = {}
        if not xml:
            return
        root = ET.fromstring(xml)
        for ab in root.iter(_W + "abstractNum"):
            lv: dict[int, tuple[str, str, int]] = {}
            for lvl in ab.iter(_W + "lvl"):
                i = int(_attr(lvl, "ilvl") or 0)
                fmt = _attr(lvl.find(_W + "numFmt"), "val") or "decimal"
                text = _attr(lvl.find(_W + "lvlText"), "val")
                start = int(_attr(lvl.find(_W + "start"), "val") or 1)
                lv[i] = (fmt, text, start)
            self.levels[_attr(ab, "abstractNumId")] = lv
        for num in root.iter(_W + "num"):
            self.num_to_abs[_attr(num, "numId")] = _attr(num.find(_W + "abstractNumId"), "val")

    def label(self, num_id: str, ilvl: int) -> str:
        lv = self.levels.get(self.num_to_abs.get(num_id, ""), {})
        if num_id == "0" or not lv:
            return "-"
        fmt, text, _start = lv.get(ilvl, ("bullet", "", 1))
        if fmt in ("bullet", "none"):
            return "-"
        cnt = self.counters.setdefault(num_id, [0] * 9)
        for i in range(9):
            if not cnt[i] and i < ilvl:
                cnt[i] = lv.get(i, ("decimal", "", 1))[2]
        cnt[ilvl] = cnt[ilvl] + 1 if cnt[ilvl] else lv.get(ilvl, ("decimal", "", 1))[2]
        for i in range(ilvl + 1, 9):
            cnt[i] = 0                     # cấp con đếm lại khi cấp cha sang mục mới
        out = text or f"%{ilvl + 1}."
        for i in range(9):
            if f"%{i + 1}" in out:
                f = lv.get(i, ("decimal", "", 1))[0]
                out = out.replace(f"%{i + 1}", _fmt_num(cnt[i] or 1, f))
        return out.strip() or "-"


# ── Đoạn văn ────────────────────────────────────────────────────────────────
def _runs_text(p: ET.Element) -> str:
    """Nối chữ trong đoạn, bọc **đậm** cho cụm chữ đậm liền nhau."""
    parts: list[tuple[str, bool]] = []
    for r in p.iter(_W + "r"):
        rpr = r.find(_W + "rPr")
        bold = _on(rpr.find(_W + "b")) if rpr is not None else False
        buf = ""
        for ch in r:
            if ch.tag == _W + "t":
                buf += ch.text or ""
            elif ch.tag == _W + "tab":
                buf += " "
            elif ch.tag in (_W + "br", _W + "cr"):
                buf += "\n"
        if buf:
            if parts and parts[-1][1] == bold:
                parts[-1] = (parts[-1][0] + buf, bold)
            else:
                parts.append((buf, bold))
    out = ""
    for txt, bold in parts:
        if bold and txt.strip():
            lead = txt[: len(txt) - len(txt.lstrip())]
            tail = txt[len(txt.rstrip()):]
            out += f"{lead}**{txt.strip()}**{tail}"
        else:
            out += txt
    return re.sub(r"[ \t]+", " ", out).strip()


def _heading_level(p: ET.Element, styles: dict[str, str]) -> int:
    ppr = p.find(_W + "pPr")
    if ppr is None:
        return 0
    name = styles.get(_attr(ppr.find(_W + "pStyle"), "val"), "").lower()
    if name == "title":
        return 1
    m = re.match(r"heading\s*(\d)", name)
    if m:
        return min(int(m.group(1)) + 1, 6)       # '#' dành cho tên văn bản
    lvl = _attr(ppr.find(_W + "outlineLvl"), "val")
    return min(int(lvl) + 2, 6) if lvl.isdigit() and int(lvl) < 6 else 0


def _paragraph(p: ET.Element, styles: dict[str, str], style_nums: dict[str, tuple[str, int]],
               numbering: _Numbering) -> str:
    text = _runs_text(p)
    if not text:
        return ""
    plain = text.replace("**", "")
    level = _heading_level(p, styles)
    if not level:
        for rx, lv in _STRUCT:
            if rx.match(plain):
                level = lv
                break
    if level:
        return "#" * level + " " + plain.replace("\n", " ")
    ppr = p.find(_W + "pPr")
    numpr = ppr.find(_W + "numPr") if ppr is not None else None
    style_num = style_nums.get(_attr(ppr.find(_W + "pStyle"), "val")) if ppr is not None else None
    if numpr is not None or style_num:
        num_id, ilvl = style_num or ("", 0)
        if numpr is not None:
            num_id = _attr(numpr.find(_W + "numId"), "val") or num_id
            ilvl = int(_attr(numpr.find(_W + "ilvl"), "val") or ilvl)
        return "  " * ilvl + numbering.label(num_id, ilvl) + " " + text
    return text


def _table(tbl: ET.Element) -> str:
    rows: list[list[str]] = []
    for tr in tbl.findall(_W + "tr"):
        cells = []
        for tc in tr.findall(_W + "tc"):
            txt = " ".join(t for t in (_runs_text(p) for p in tc.iter(_W + "p")) if t)
            cells.append(txt.replace("|", "\\|").replace("\n", " "))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _styles(xml: bytes | None) -> tuple[dict[str, str], dict[str, tuple[str, int]]]:
    """(styleId -> tên style, styleId -> (numId, ilvl)). Style danh sách của Word ('List
    Number', 'List Bullet') gắn số thứ tự ở STYLE chứ không ở từng đoạn."""
    if not xml:
        return {}, {}
    root = ET.fromstring(xml)
    names: dict[str, str] = {}
    nums: dict[str, tuple[str, int]] = {}
    for st in root.iter(_W + "style"):
        sid = _attr(st, "styleId")
        names[sid] = _attr(st.find(_W + "name"), "val")
        numpr = st.find(f"{_W}pPr/{_W}numPr")
        if numpr is not None and _attr(numpr.find(_W + "numId"), "val"):
            nums[sid] = (_attr(numpr.find(_W + "numId"), "val"),
                         int(_attr(numpr.find(_W + "ilvl"), "val") or 0))
    return names, nums


def docx_to_markdown(data: bytes, title: str = "") -> str:
    """Nội dung một tệp .docx (bytes) -> văn bản Markdown.

    `title`: tên văn bản đặt ở dòng '# ' đầu tiên (kho quy định lấy tiêu đề đầu tiên làm
    tên hiển thị trong trích dẫn). Rỗng và văn bản không có style Title -> không thêm."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        body_xml = _read_part(zf, "word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("Không phải tệp .docx hợp lệ (tệp .doc cũ hãy lưu lại thành .docx).") from exc

    def opt(name: str) -> bytes | None:
        try:
            return _read_part(zf, name)
        except KeyError:
            return None

    styles, style_nums = _styles(opt("word/styles.xml"))
    numbering = _Numbering(opt("word/numbering.xml"))
    body = ET.fromstring(body_xml).find(_W + "body")
    blocks: list[str] = []
    for el in (list(body) if body is not None else []):
        if el.tag == _W + "p":
            blk = _paragraph(el, styles, style_nums, numbering)
        elif el.tag == _W + "tbl":
            blk = _table(el)
        else:
            continue
        if blk:
            blocks.append(blk)
    if not blocks and b"<w:drawing" in body_xml:
        # Word chỉ chứa ẢNH chụp/quét từng trang (văn bản dán dạng hình) — không có chữ để
        # chuyển. Báo rõ thay vì trả về một tệp .md rỗng rồi nạp vào kho mà không ai hay.
        raise ValueError("Tệp Word chỉ chứa ảnh (bản quét), không có chữ để chuyển — cần "
                         "bản Word/PDF có lớp chữ, hoặc đọc ảnh bằng OCR trước.")
    if title and not (blocks and blocks[0].startswith("# ")):
        blocks.insert(0, f"# {title.strip()}")
    return "\n\n".join(blocks).strip() + "\n"


# TRẦN một phần XML sau giải nén. .docx là tệp ZIP: vài trăm KB nén có thể bung ra hàng
# GB ("bom nén") và `zf.read` nạp trọn vào RAM. `zipfile` đọc đúng `file_size` khai trong
# mục lục rồi kiểm CRC, nên chặn theo con số khai là chặn được cả lượng thật sự bung ra.
MAX_PART_BYTES = 64 * 1048576


def _read_part(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)           # KeyError khi thiếu phần — nơi gọi xử lý
    if info.file_size > MAX_PART_BYTES:
        raise ValueError(f"Tệp Word quá lớn sau giải nén ({name} > "
                         f"{MAX_PART_BYTES // 1048576} MB).")
    return zf.read(info)


def _safe_stem(name: str) -> str:
    """Tên tệp .md gọn: bỏ dấu, chữ thường, chỉ a-z 0-9 _ -."""
    import unicodedata  # noqa: PLC0415

    s = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9_-]+", "_", s).strip("_") or "van_ban"


def main(argv: list[str] | None = None) -> int:
    from app.domain.regulations.corpus import RULES_DIR  # noqa: PLC0415

    ap = argparse.ArgumentParser(prog="docx2md", description="Chuyển .docx sang .md cho kho quy định.")
    ap.add_argument("inputs", nargs="+", help="Tệp .docx (một hoặc nhiều)")
    ap.add_argument("-o", "--output", help="Tệp .md đầu ra (chỉ khi có MỘT tệp vào)")
    ap.add_argument("--rules", action="store_true",
                    help="Ghi thẳng vào app/rules/ (sau đó chạy npm run seed để nạp vào kho)")
    ap.add_argument("--title", default="", help="Tên văn bản (mặc định: tên tệp)")
    args = ap.parse_args(argv)
    if args.output and len(args.inputs) > 1:
        ap.error("-o chỉ dùng khi chuyển MỘT tệp.")

    # `npm run` đổi thư mục làm việc sang backend/ — đường dẫn tương đối của người dùng
    # phải tính theo nơi họ GÕ lệnh (npm ghi lại ở INIT_CWD).
    base = Path(os.environ.get("INIT_CWD") or os.getcwd())
    status = 0
    for raw in args.inputs:
        src = Path(raw) if Path(raw).is_absolute() else base / raw
        if src.suffix.lower() != ".docx":
            print(f"[docx2md] BỎ QUA {src.name}: chỉ nhận .docx (tệp .doc hãy lưu lại thành .docx).")
            status = 1
            continue
        try:
            md = docx_to_markdown(src.read_bytes(), args.title or src.stem.replace("_", " "))
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"[docx2md] LỖI {src.name}: {exc}")
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
        print(f"[docx2md] {src.name} -> {out}")
    if args.rules and status == 0:
        print("[docx2md] Đã ghi vào app/rules/. Chạy `npm run seed` (hoặc lưu ở trang Quản trị) "
              "để nạp vào kho quy định; vào Quản trị > Kho quy định để phê duyệt.")
    return status


if __name__ == "__main__":
    sys.exit(main())
