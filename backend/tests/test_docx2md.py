"""Test CHUYỂN WORD -> MARKDOWN cho kho quy định.

Tệp .docx được DỰNG TẠI CHỖ bằng zipfile (một .docx chỉ là gói ZIP chứa XML), không
kèm tệp mẫu vào cây dự án.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.domain.regulations import docx2md
from app.domain.regulations.ingest import chunk_markdown

_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text: str, style: str = "", num: tuple[str, int] | None = None, bold: bool = False) -> str:
    ppr = ""
    if style or num:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if num:
            ppr += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
        ppr += "</w:pPr>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _docx(body: str, styles: str = "", numbering: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {_NS}><w:body>{body}</w:body></w:document>")
        if styles:
            z.writestr("word/styles.xml", f"<w:styles {_NS}>{styles}</w:styles>")
        if numbering:
            z.writestr("word/numbering.xml", f"<w:numbering {_NS}>{numbering}</w:numbering>")
    return buf.getvalue()


_STYLES = ('<w:style w:styleId="Title"><w:name w:val="Title"/></w:style>'
           '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>')
_NUMBERING = (
    '<w:abstractNum w:abstractNumId="1">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/></w:lvl>'
    '</w:abstractNum><w:num w:numId="5"><w:abstractNumId w:val="1"/></w:num>')


def test_tieu_de_dieu_khoan_danh_so_va_dam():
    body = "".join([
        _p("Quy định mẫu về hợp đồng dịch vụ", style="Title"),
        _p("Chương I", ),
        _p("Điều 1. Phạm vi điều chỉnh"),
        _p("Quy định này áp dụng cho hợp đồng dịch vụ."),
        _p("Giá trị hợp đồng phải ghi rõ số tiền.", num=("5", 0)),
        _p("Thời hạn phải xác định.", num=("5", 0)),
        _p("ghi bằng số", num=("5", 1)),
        _p("ghi bằng chữ", num=("5", 1)),
        _p("Lưu ý quan trọng", bold=True),
        _p("Phụ lục", style="Heading1"),
    ])
    md = docx2md.docx_to_markdown(_docx(body, _STYLES, _NUMBERING))
    assert md.startswith("# Quy định mẫu về hợp đồng dịch vụ\n\n")
    assert "## Chương I" in md
    assert "### Điều 1. Phạm vi điều chỉnh" in md
    assert "1. Giá trị hợp đồng phải ghi rõ số tiền." in md
    assert "2. Thời hạn phải xác định." in md
    assert "  a) ghi bằng số" in md and "  b) ghi bằng chữ" in md
    assert "**Lưu ý quan trọng**" in md
    assert "## Phụ lục" in md
    # Mỗi khối cách nhau một dòng trống -> bộ cắt đoạn của kho quy định chia đúng ranh giới.
    assert all(c.strip() for c in chunk_markdown(md, max_chars=60))


def test_bang_thanh_bang_markdown_va_them_tieu_de():
    tbl = ("<w:tbl>"
           "<w:tr><w:tc>" + _p("Khoản") + "</w:tc><w:tc>" + _p("Mức") + "</w:tc></w:tr>"
           "<w:tr><w:tc>" + _p("Phí a|b") + "</w:tc><w:tc>" + _p("100") + "</w:tc></w:tr>"
           "</w:tbl>")
    md = docx2md.docx_to_markdown(_docx(_p("Nội dung") + tbl), title="Biểu phí mẫu")
    assert md.startswith("# Biểu phí mẫu\n\nNội dung\n\n")
    assert "| Khoản | Mức |\n|---|---|\n| Phí a\\|b | 100 |" in md


def test_word_chi_chua_anh_bao_ro():
    """Văn bản quy định hay được phát hành dạng ẢNH QUÉT dán vào Word: không có chữ để
    chuyển thì phải báo lỗi, không được ra tệp .md rỗng."""
    anh = '<w:p><w:r><w:drawing><wp:inline xmlns:wp="x"/></w:drawing></w:r></w:p>'
    with pytest.raises(ValueError, match="chỉ chứa ảnh"):
        docx2md.docx_to_markdown(_docx(anh))


def test_tep_khong_phai_docx():
    with pytest.raises(ValueError):
        docx2md.docx_to_markdown(b"khong phai zip")


def test_cli_ghi_md_canh_tep_goc_va_vao_rules(tmp_path, monkeypatch):
    src = tmp_path / "Quy định mẫu.docx"
    src.write_bytes(_docx(_p("Điều 1. Nội dung")))
    monkeypatch.setenv("INIT_CWD", str(tmp_path))
    assert docx2md.main(["Quy định mẫu.docx"]) == 0
    out = tmp_path / "Quy định mẫu.md"
    assert out.read_text(encoding="utf-8").startswith("# Quy định mẫu\n\n### Điều 1. Nội dung")

    rules = tmp_path / "rules"
    monkeypatch.setattr("app.domain.regulations.corpus.RULES_DIR", rules)
    assert docx2md.main([str(src), "--rules"]) == 0
    assert (rules / "quy_dinh_mau.md").exists()
    # .doc cũ -> bỏ qua kèm mã lỗi, không ghi gì.
    assert docx2md.main([str(tmp_path / "cu.doc")]) == 1


def test_docx_bom_nen_bi_tu_choi(monkeypatch):
    """Phần XML bung ra quá trần -> ValueError, không nạp trọn vào RAM."""
    from app.domain.regulations import docx2md

    monkeypatch.setattr(docx2md, "MAX_PART_BYTES", 1000)
    with pytest.raises(ValueError, match="quá lớn"):
        docx2md.docx_to_markdown(_docx(_p("Điều 1. " + "x" * 5000)))
