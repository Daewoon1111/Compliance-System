"""Test tầng ĐỌC ẢNH (không nạp Vintern thật): độ tin cậy theo dòng, làm sạch markdown,
cắt vòng lặp, chia đôi trang dày, tự xoay, xóa mộc, đối chứng lớp văn bản."""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from app.domain.documents.ocr import layout, vintern


# ---------------------------------------------------------------------------
# Vintern — hàm thuần
# ---------------------------------------------------------------------------
def test_line_confidences_gom_theo_dong():
    # token: "Tiền"(0.9) "lương"(0.7) "\n"(—) "5"(0.5) "\n\n"(—) "Hết"(1.0)
    newlines = [0, 0, 1, 0, 2, 0]
    probs = [0.9, 0.7, 0.99, 0.5, 0.99, 1.0]
    assert vintern.line_confidences(newlines, probs) == [0.8, 0.5, 0.0, 1.0]


def test_to_lines_lam_sach_markdown_va_bang():
    text = "# HỢP ĐỒNG\n**Tiền lương**: 150.000 JPY\n| Nhãn | Giá trị |\n|---|---|\n| Ký quỹ | 0 VND |\n| A | B | C |\n```"
    lines, looped = vintern.to_lines(text, [0.9] * 7)
    assert [ln["text"] for ln in lines] == [
        "HỢP ĐỒNG", "Tiền lương: 150.000 JPY", "Nhãn: Giá trị", "Ký quỹ: 0 VND", "A | B | C"]
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
        {"text": "技能実習生 制度", "conf": 0.9}, {"text": "Tiền lương 月給 150.000", "conf": 0.9}])
    lines, meta = layout.ocr_image_lines(_trang_chu(), with_meta=True)
    assert [ln["text"] for ln in lines] == ["Tiền lương 150.000"]
    assert meta["rotate_k"] == 0 and meta["w"] == 1240


# ---------------------------------------------------------------------------
# pipeline — cửa sổ trang, đối chứng lớp văn bản
# ---------------------------------------------------------------------------
def test_page_window_lay_lan_xuat_hien_dau_tien():
    from app.domain.documents.pipeline import _page_window

    ln = lambda t: {"text": t}  # noqa: E731
    pages = [
        {"index": 0, "lines": [ln("HỢP ĐỒNG CUNG ỨNG LAO ĐỘNG")]},
        {"index": 1, "lines": [ln("Điều 3. Tiền dịch vụ: 30.000.000 VND")]},
        {"index": 2, "lines": [ln("Phụ lục kèm theo Hợp đồng cung ứng lao động số 12")]},
    ]
    assert _page_window(pages, None)[:2] == (0, 2)


def test_text_agreement():
    from app.domain.documents.pipeline import text_agreement

    same = "Tiền lương cơ bản 150.000 JPY mỗi tháng làm việc tại Aichi"
    assert text_agreement(same, same) == 1.0
    assert text_agreement(same, "Tien luong co ban 150.000 JPY moi thang lam viec tai Aichi") == 1.0
    assert text_agreement(same, "Người lao động nộp phí môi giới 5.000 USD") < 0.3


def test_lop_van_ban_lech_anh_bi_bo(monkeypatch):
    from app.domain.documents import pipeline

    hidden = [{"text": "Không thu bất kỳ khoản phí nào của người lao động", "conf": 1.0}] * 3
    plan = {"total": 2, "from_text": [0, 1], "need_ocr": [],
            "pages": [{"index": i, "lines": hidden, "chars": 300, "meta": {"rotate_k": 0}}
                      for i in (0, 1)]}
    visible = [{"text": "Phí môi giới người lao động nộp: 5.000 USD", "conf": 0.9}]
    monkeypatch.setattr(pipeline, "render_pages",
                        lambda data, idx=None, dpi=None: iter([(i, object()) for i in (idx or [])]))
    monkeypatch.setattr(pipeline, "ocr_image_lines", lambda img, with_meta=False: (visible, {"rotate_k": 0}))
    monkeypatch.setattr(pipeline, "progress_update", lambda *a, **k: None)
    pages, _meta, from_text, ocred, verify = pipeline._read_hybrid(b"", plan, "x.pdf", "", 1, 1, "")
    assert verify["accepted"] is False and from_text == 0 and ocred == 2
    assert all(pg == visible for pg in pages)


def test_spelling_restores_vietnamese_diacritics():
    """C2 — khôi phục dấu bằng từ điển dựng từ corpus luật (không LLM)."""
    from app.domain.documents.spelling import restore_diacritics
    out = restore_diacritics("Ngui s dng lao dng phi t chc hun luyn an toan")
    assert "sử dụng lao động" in out and "huấn luyện" in out
    # Văn bản đã đúng chính tả -> KHÔNG bị đổi
    ok = "Người sử dụng lao động phải tổ chức huấn luyện an toàn, vệ sinh lao động"
    assert restore_diacritics(ok) == ok
    # Không đủ căn cứ -> giữ nguyên (không đoán bừa)
    assert restore_diacritics("Osawa Haruda Iga-shi") == "Osawa Haruda Iga-shi"


def test_phrase_bank_canonicalizes_value():
    """C1 — giá trị OCR gần một cụm chuẩn -> thay bằng bản chuẩn, evidence giữ bản gốc."""
    from app.domain.documents.spelling import canonicalize_fields
    raw = "TTS duc tham gia cac loi bo him theo quy dnh ca phap lut Nht Bn"
    c = {
        "extracted_fields": {"cac_che_do_bao_hiem": {
            "value": raw, "confidence": 0.42, "evidence": {}}},
        "missing_fields": [],
        "raw": {"normalized_text": raw},
    }
    out = canonicalize_fields(c, "nhat_ban")["extracted_fields"]["cac_che_do_bao_hiem"]
    assert out["value"].startswith("Người lao động được tham gia các loại bảo hiểm")
    assert out["evidence"]["source"] == "PHRASE_BANK"
    assert out["evidence"]["short_quote"]          # vẫn còn dấu vết OCR gốc


def test_phrase_bank_fixes_value_that_differs_ONLY_by_diacritics():
    """C1 — giá trị chỉ khác bản chuẩn ở CHỖ THIẾU DẤU vẫn phải được nắn.

    Guard cũ so bản BỎ DẤU (`_fold(cand) != _fold(val)`) nên bản rụng dấu hoàn toàn
    bị coi là 'đã giống rồi' và KHÔNG được sửa — đúng ngay loại hỏng phổ biến nhất
    của OCR mà ngân hàng cụm sinh ra để chữa. Nay so NGUYÊN VĂN."""
    from app.domain.documents.ocr import fold_diacritics
    from app.domain.documents.spelling import canonicalize_fields, phrases_for_job

    canon = phrases_for_job("nhat_ban")["an_toan_ve_sinh_lao_dong"][0]
    broken = fold_diacritics(canon)                # bản rụng sạch dấu
    assert broken != canon
    c = {
        "extracted_fields": {"an_toan_ve_sinh_lao_dong": {
            "value": broken, "confidence": 0.5, "evidence": {}}},
        "missing_fields": [],
        "raw": {"normalized_text": "- " + broken},
    }
    out = canonicalize_fields(c, "nhat_ban")["extracted_fields"]["an_toan_ve_sinh_lao_dong"]
    assert out["value"] == canon                    # đã nắn về đúng bản chuẩn
    assert out["evidence"]["short_quote"] == broken  # bằng chứng giữ bản OCR gốc


def test_split_sections_tolerates_ocr_damage():
    """B3 — cắt vùng theo mục biểu mẫu, chịu được tiêu đề bị OCR rụng chữ."""
    from app.domain.documents.rules import split_sections
    from app.store import load_extraction_config
    txt = ("3. Ni dung:\n- Đa đim làm vic: 3090 Osawa\n"
           "11. Chi phí ngưi lao đng phi tr:\n- Tin dch v: 25.000.000 VND\n12. Tin ký qu: 0\n")
    sec = split_sections(txt, load_extraction_config())
    assert "Osawa" in sec["noi_dung"] and "25.000.000" not in sec["noi_dung"]
    assert "25.000.000" in sec["chi_phi_nld"]


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


# ---------------------------------------------------------------------------
# GOLDEN — OCR: chuẩn hóa thời giờ làm việc từ text OCR rụng dấu
# ---------------------------------------------------------------------------
def test_golden_worktime_normalized_from_ocr_text():
    from app.domain.documents.rules import normalize_worktime
    golden = {
        "8 gio/ngay, 40 gio/tuan": "8 giờ/ngày; 40 giờ/tuần",
        "44 gio / tuan": "44 giờ/tuần",
    }
    for raw, want in golden.items():
        assert normalize_worktime(raw) == want, raw


def test_foreign_majority_line_dropped_and_stray_chars_stripped():
    from app.domain.documents.ocr.text import (
        _is_foreign_script_line,
        _strip_foreign_chars,
    )
    assert _is_foreign_script_line("技能実習生 制度") is True            # thuần Nhật -> bỏ
    assert _is_foreign_script_line("第3条 Article") is False            # Latin đa số -> giữ
    assert _is_foreign_script_line("Tiền lương 月給") is False
    assert _strip_foreign_chars("200.000円/月 JPY") == "200.000 / JPY"  # xóa ký tự lẻ
