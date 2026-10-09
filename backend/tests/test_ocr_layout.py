"""Test tầng ĐỌC ẢNH (không nạp Vintern thật): độ tin cậy theo dòng, làm sạch markdown,
cắt vòng lặp, chia đôi trang dày, tự xoay, xóa mộc, đối chứng lớp văn bản, neo cắt văn
bản, khôi phục dấu."""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from app.domain.documents.ocr import layout, vintern


# ---------------------------------------------------------------------------
# Vintern — hàm thuần
# ---------------------------------------------------------------------------
def test_line_confidences_gom_theo_dong():
    # token: "Giá"(0.9) "trị"(0.7) "\n"(—) "5"(0.5) "\n\n"(—) "Hết"(1.0)
    newlines = [0, 0, 1, 0, 2, 0]
    probs = [0.9, 0.7, 0.99, 0.5, 0.99, 1.0]
    assert vintern.line_confidences(newlines, probs) == [0.8, 0.5, 0.0, 1.0]


def test_to_lines_lam_sach_markdown_va_bang():
    text = ("# HỢP ĐỒNG\n**Giá trị hợp đồng**: 120.000.000 VND\n| Nhãn | Giá trị |\n|---|---|\n"
            "| Thuế GTGT | 0 VND |\n| A | B | C |\n```")
    lines, looped = vintern.to_lines(text, [0.9] * 7)
    assert [ln["text"] for ln in lines] == [
        "HỢP ĐỒNG", "Giá trị hợp đồng: 120.000.000 VND", "Nhãn: Giá trị", "Thuế GTGT: 0 VND",
        "A | B | C"]
    assert not looped and all(ln["conf"] == 0.9 for ln in lines)


def test_to_lines_cat_vong_lap():
    text = "Điều 1\n" + "Điều 2\n" * 8 + "Điều 3"
    lines, looped = vintern.to_lines(text)
    assert looped
    assert [ln["text"] for ln in lines] == ["Điều 1", "Điều 2", "Điều 2", "Điều 2", "Điều 3"]


def test_join_halves_bo_dong_trung_mep():
    top = [{"text": t, "conf": 1} for t in ("a", "b", "c")]
    bottom = [{"text": t, "conf": 1} for t in ("c", "d")]
    assert [x["text"] for x in vintern.join_halves(top, bottom)] == ["a", "b", "c", "d"]


def test_tile_image_so_o_va_anh_thu_nho():
    a4 = Image.new("RGB", (1654, 2339), "white")
    tiles = vintern.tile_image(a4, 6)
    assert 2 <= len(tiles) <= 7 and all(t.size == (448, 448) for t in tiles)
    assert len(vintern.tile_image(a4, 1)) == 1          # 1 ô thì không thêm ảnh thu nhỏ


def test_transcribe_chia_doi_khi_cham_tran(monkeypatch):
    calls = []

    def fake(image, tiles, max_new):
        calls.append(image.size)
        if len(calls) == 1:
            return "dòng cụt", [0.9], True              # chạm trần token
        return ("nửa trên\nmép" if len(calls) == 2 else "mép\nnửa dưới"), [0.8, 0.8], False

    monkeypatch.setattr(vintern, "_generate", fake)
    out = vintern.transcribe(Image.new("RGB", (1000, 1400), "white"), max_tiles=2)
    assert [ln["text"] for ln in out] == ["nửa trên", "mép", "nửa dưới"]
    assert len(calls) == 3 and calls[1][1] < 1400


def test_repetition_penalty_mac_dinh_nhe():
    """Phạt lặp mạnh (2.5 như model card) đổi cả cụm số tiền lặp thật."""
    from app.core import Settings

    assert Settings.model_fields["vintern_repetition_penalty"].default <= 1.1
    assert len(Settings.model_fields["vintern_revision"].default) == 40   # revision ghim


def test_khong_con_setting_paddle():
    from app.core import Settings

    stale = {"ocr_det_limit_side_len", "ocr_model_tier", "ocr_enable_hpi", "ocr_enable_mkldnn",
             "ocr_cpu_threads", "ocr_preprocess", "ocr_roi_rescan", "ocr_roi_dpi",
             "ocr_roi_max_fields", "ocr_crop_top_frac", "ocr_crop_bottom_frac",
             "ocr_workers", "ocr_parallel_min_pages", "ocr_device", "ocr_gpu_mem_fraction"}
    assert not stale & set(Settings.model_fields)


# ---------------------------------------------------------------------------
# layout — xoay trang, xóa mộc
# ---------------------------------------------------------------------------
def _trang_chu(kind: str = "text") -> Image.Image:
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    for i, y in enumerate(range(150, 1600, 45)):
        if kind == "form":
            d.rectangle((120, y, 420, y + 18), fill="black")
            d.rectangle((700, y, 900, y + 18), fill="black")
        else:
            for x in range(120, 1100, 70 + (i % 3) * 10):
                d.rectangle((x, y, x + 50, y + 18), fill="black")
    return img


@pytest.mark.parametrize("kind", ["text", "form"])
def test_ink_axis_ratio_phan_biet_trang_nam_ngang(kind):
    page = _trang_chu(kind)
    assert layout.ink_axis_ratio(page) > 1.5
    assert layout.ink_axis_ratio(page.rotate(90, expand=True)) < 0.7


def test_choose_rotation_chon_chieu_doc_duoc(monkeypatch):
    sideways = _trang_chu().rotate(90, expand=True)
    scores = iter([0.2, 0.9])                          # k=1 kém, k=3 tốt
    monkeypatch.setattr(vintern, "quick_confidence", lambda img: next(scores))
    assert layout.choose_rotation(sideways) == 3
    monkeypatch.setattr(vintern, "quick_confidence", lambda img: pytest.fail("không được gọi"))
    assert layout.choose_rotation(_trang_chu()) == 0   # trang thẳng: không tốn lượt đọc


def test_remove_red_stamp_giu_chu_den():
    img = Image.new("RGB", (100, 100), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, 40, 40), fill=(200, 30, 30))
    d.rectangle((60, 60, 90, 90), fill=(10, 10, 10))
    out = layout.remove_red_stamp(img)
    assert out.getpixel((20, 20)) == (255, 255, 255)
    assert out.getpixel((70, 70)) == (10, 10, 10)


def test_ocr_image_lines_loc_chu_nuoc_ngoai(monkeypatch):
    monkeypatch.setattr(layout, "choose_rotation", lambda img: 0)
    monkeypatch.setattr(vintern, "transcribe", lambda img: [
        {"text": "契約書 制度", "conf": 0.9}, {"text": "Giá trị 金額 120.000.000", "conf": 0.9}])
    lines, meta = layout.ocr_image_lines(_trang_chu(), with_meta=True)
    assert [ln["text"] for ln in lines] == ["Giá trị 120.000.000"]
    assert meta["rotate_k"] == 0 and meta["w"] == 1240


# ---------------------------------------------------------------------------
# pipeline — cửa sổ trang, đối chứng lớp văn bản
# ---------------------------------------------------------------------------
def test_page_window_lay_lan_xuat_hien_dau_tien():
    from app.domain.documents.pipeline import _page_window

    ln = lambda t: {"text": t}  # noqa: E731
    pages = [
        {"index": 0, "lines": [ln("Công ty TNHH Minh Phát")]},
        {"index": 1, "lines": [ln("HỢP ĐỒNG DỊCH VỤ"), ln("Giá trị hợp đồng: 120.000.000 VND")]},
        {"index": 2, "lines": [ln("Phụ lục kèm theo Hợp đồng dịch vụ số 15/2025/HĐDV")]},
        {"index": 3, "lines": []},                       # trang ảnh: luôn trong cửa sổ
    ]
    assert _page_window(pages, None)[:2] == (0, 3)
    # Neo bắt đầu: lấy lần xuất hiện ĐẦU TIÊN (trang 1), lần sau trong phụ lục không kéo đi.
    assert _page_window(pages, "hợp đồng dịch vụ") == (1, 3, False)


def test_text_agreement():
    from app.domain.documents.pipeline import text_agreement

    same = "Giá trị hợp đồng 120.000.000 VND thanh toán bằng chuyển khoản trong 30 ngày"
    assert text_agreement(same, same) == 1.0
    assert text_agreement(
        same, "Gia tri hop dong 120.000.000 VND thanh toan bang chuyen khoan trong 30 ngay") == 1.0
    assert text_agreement(same, "Bên B chịu phạt vi phạm 500.000.000 USD") < 0.3
    assert text_agreement("", same) == 0.0


def test_lop_van_ban_lech_anh_bi_bo(monkeypatch):
    from app.domain.documents import pipeline

    hidden = [{"text": "Giá trị hợp đồng: 120.000.000 VND, đã gồm thuế", "conf": 1.0}] * 3
    plan = {"total": 2, "from_text": [0, 1], "need_ocr": [],
            "pages": [{"index": i, "lines": hidden, "chars": 300, "meta": {"rotate_k": 0}}
                      for i in (0, 1)]}
    visible = [{"text": "Bên B chịu phạt vi phạm: 500.000.000 USD", "conf": 0.9}]
    monkeypatch.setattr(pipeline, "render_pages",
                        lambda data, idx=None, dpi=None: iter([(i, object()) for i in (idx or [])]))
    monkeypatch.setattr(pipeline, "ocr_image_lines", lambda img, with_meta=False: (visible, {"rotate_k": 0}))
    monkeypatch.setattr(pipeline, "progress_update", lambda *a, **k: None)
    pages, _meta, from_text, ocred, verify = pipeline._read_hybrid(b"", plan, "x.pdf", "", 1, 1, "")
    assert verify["accepted"] is False and from_text == 0 and ocred == 2
    assert all(pg == visible for pg in pages)


# ---------------------------------------------------------------------------
# spelling — khôi phục dấu bằng từ điển (không mô hình)
# ---------------------------------------------------------------------------
_CORPUS = [
    "Phương thức thanh toán: chuyển khoản. Giải quyết tranh chấp tại Tòa án nhân dân có thẩm quyền.",
    "Hai bên thống nhất phương thức thanh toán và giải quyết tranh chấp theo hợp đồng.",
    "Giá trị hợp đồng đã bao gồm thuế giá trị gia tăng.",
]


@pytest.fixture()
def tu_dien(monkeypatch):
    """Từ điển dựng từ corpus CỐ ĐỊNH — test không phụ thuộc kho quy định đang có gì."""
    from app.domain.documents import spelling

    monkeypatch.setattr(spelling, "_corpus_texts", lambda: list(_CORPUS))
    spelling.reset_lexicon()
    yield spelling
    spelling.reset_lexicon()        # trả từ điển THẬT cho test sau


def test_spelling_restores_vietnamese_diacritics(tu_dien):
    out = tu_dien.restore_diacritics("Phng thc thanh toan: chuyn khon")
    assert out == "Phương thức thanh toán: chuyển khoản"
    assert tu_dien.restore_diacritics("Gii quyt tranh chp ti Toa an nhan dan") == (
        "Giải quyết tranh chấp tại Tòa án nhân dân")
    # Văn bản đã đúng chính tả -> KHÔNG bị đổi
    ok = "Giá trị hợp đồng đã bao gồm thuế giá trị gia tăng."
    assert tu_dien.restore_diacritics(ok) == ok
    # Không đủ căn cứ -> giữ nguyên (không đoán bừa)
    assert tu_dien.restore_diacritics("Osawa Haruda Iga-shi") == "Osawa Haruda Iga-shi"


def test_tu_da_co_dau_khong_bi_doi(tu_dien, monkeypatch):
    monkeypatch.setattr(tu_dien, "_corpus_texts", lambda: ["Thường xuyên kiểm tra hạ tầng."])
    tu_dien.reset_lexicon()
    ok = "Công ty Thương mại tại Hà Nội"
    assert tu_dien.restore_diacritics(ok) == ok


def test_reset_lexicon_dung_lai_tu_dien(tu_dien, monkeypatch):
    """Kho quy định/bộ trường đổi -> phải dựng lại từ điển, không dùng bản cũ trong cache."""
    assert tu_dien.restore_diacritics("chuyn khon") == "chuyển khoản"
    monkeypatch.setattr(tu_dien, "_corpus_texts", lambda: ["Bàn giao tài liệu."])
    assert tu_dien.restore_diacritics("chuyn khon") == "chuyển khoản"   # còn cache
    tu_dien.reset_lexicon()
    assert tu_dien.restore_diacritics("chuyn khon") == "chuyn khon"


def test_restore_field_spelling_giu_bang_chung_goc(tu_dien):
    """Giá trị VĂN BẢN được sửa dấu; bằng chứng giữ nguyên văn OCR; ngày/số không bị đụng."""
    raw = "Chuyn khon, chia lam 2 dot"
    c = {"extracted_fields": {
        "phuong_thuc_thanh_toan": {"value": raw, "evidence": {"short_quote": None}},
        "ngay_ky": {"value": "2025-03-05", "evidence": {}},
        "so_luong": {"value": 12, "evidence": {}},
        "gia_tri_hop_dong": {"value": {"amount": 120000000, "note": "da bao gm thue"}},
    }}
    ef = tu_dien.restore_field_spelling(c)["extracted_fields"]
    assert ef["phuong_thuc_thanh_toan"]["value"].startswith("Chuyển khoản")
    assert ef["phuong_thuc_thanh_toan"]["evidence"]["short_quote"] == raw
    assert ef["ngay_ky"]["value"] == "2025-03-05" and ef["so_luong"]["value"] == 12
    assert ef["gia_tri_hop_dong"]["value"]["note"] == "đã bao gồm thuế"


def test_broken_checks_config_is_loud_not_silent():
    """checks.json hỏng = MẤT SẠCH kiểm tra tất định + danh mục khoản thu bị cấm,
    mà hệ vẫn chạy và vẫn ra kết luận. Bắt buộc phải in cảnh báo, không nuốt."""
    from pathlib import Path

    from app.store import config as cfg

    real = cfg.SERVICES_DIR
    try:
        cfg.SERVICES_DIR = Path("/khong-ton-tai-o-dau-ca")
        cfg._load_checks.cache_clear()      # cache 1 lần/tiến trình -> phải dọn mới đọc lại
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cfg._load_checks() == {}
        assert "THIẾU" in buf.getvalue() or "KHÔNG đọc được" in buf.getvalue()
    finally:
        cfg.SERVICES_DIR = real
        cfg._load_checks.cache_clear()      # trả cache về cấu hình THẬT cho test sau


def test_foreign_majority_line_dropped_and_stray_chars_stripped():
    from app.domain.documents.ocr.text import (
        _is_foreign_script_line,
        _strip_foreign_chars,
    )
    assert _is_foreign_script_line("契約書 制度") is True                # thuần chữ Hán -> bỏ
    assert _is_foreign_script_line("第3条 Article") is False            # Latin đa số -> giữ
    assert _is_foreign_script_line("Giá trị hợp đồng 金額") is False
    assert _strip_foreign_chars("200.000円/月 USD") == "200.000 / USD"  # xóa ký tự lẻ


def test_neo_bat_dau_va_ket_thuc_cat_dung_doan(monkeypatch):
    """Neo BẮT ĐẦU giữ vài dòng phía trên (Số/ngày nằm trong khối tiêu ngữ); neo KẾT
    THÚC chỉ tính ở ĐẦU DÒNG — câu giữa văn bản không được cắt cụt phần sau."""
    from app.core import settings
    from app.domain.documents.ocr import apply_end_anchor, apply_start_anchor, end_anchor_hit

    monkeypatch.setattr(settings, "ocr_start_anchor_lookback", 1)
    monkeypatch.setattr(settings, "ocr_end_anchor", "đại diện bên a")
    lines = ["Công ty TNHH Minh Phát", "Số: 15/2025/HĐDV", "HỢP ĐỒNG DỊCH VỤ",
             "Điều 5. Hợp đồng chấm dứt khi đại diện bên A ký biên bản thanh lý",
             "Điều 9. ĐẠI DIỆN BÊN A", "(ký, đóng dấu)"]
    kept, found = apply_start_anchor(lines, "hop dong dich vu")
    assert found and kept[0] == "Số: 15/2025/HĐDV"
    assert apply_start_anchor(lines, "bien ban nghiem thu") == (lines, False)
    cut, hit = apply_end_anchor(kept)
    assert hit and cut[-1].startswith("Điều 5.")
    assert end_anchor_hit(["ĐẠI DIỆN BÊN A"]) and not end_anchor_hit(lines[:4])


def test_trang_trang_khong_dua_vao_mo_hinh(monkeypatch):
    """Vintern BỊA chữ trên trang trắng -> trang trắng phải bị chặn trước khi gọi mô hình."""
    from PIL import Image, ImageDraw

    from app.domain.documents.ocr import layout

    blank = Image.new("RGB", (1240, 1754), "white")
    assert layout.is_blank(blank)
    text = blank.copy()
    ImageDraw.Draw(text).rectangle([200, 300, 900, 330], fill="black")   # ~ một dòng chữ
    assert not layout.is_blank(text)
    monkeypatch.setattr(layout.vintern, "transcribe", lambda *_a, **_k: pytest.fail("đã gọi mô hình"))
    assert layout.ocr_image_lines(blank) == []
    lines, meta = layout.ocr_image_lines(blank, with_meta=True)
    assert lines == [] and meta["rotate_k"] == 0
