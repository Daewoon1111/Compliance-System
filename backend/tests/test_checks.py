"""Test kiểm tra TẤT ĐỊNH + cờ chất lượng + vai trò tài liệu (code thuần, không LLM)."""
import pytest

from app.domain.compliance.dossier import (
    _nganh_nghe_line,
    analyze_dossier,
    classify_role,
)
from app.domain.compliance.factual import (
    DETERMINISTIC_TYPES,
    check_deposit,
    run_deterministic_check,
)
from app.domain.compliance.quality import (
    _fmt_band,
    _salary_flags,
    blocking_fields,
    signed_date_of,
)
from app.store import load_dossier_rules


def test_positive_integer_pass_fail():
    v, _ = run_deterministic_check("positive_integer", 50)
    assert v == "PASS"
    v, _ = run_deterministic_check("positive_integer", "50 người")
    assert v == "PASS"
    v, _ = run_deterministic_check("positive_integer", -3)
    assert v == "FAIL"


def test_unknown_check_type_returns_none():
    assert run_deterministic_check("khong_ton_tai", 1) is None
    assert "positive_integer" in DETERMINISTIC_TYPES


def test_check_deposit_forbidden_market():
    policy = {"default": {"allowed": False, "max_vnd": 0}}
    v, _ = check_deposit(5_000_000, "nhat_ban", policy)
    assert v == "FAIL"
    v, _ = check_deposit(0, "nhat_ban", policy)
    assert v == "PASS"


def test_check_deposit_khong_thu_bang_chu():
    # Ghi bằng chữ "không thu" -> coi như 0 -> PASS với thị trường cấm ký quỹ.
    policy = {"default": {"allowed": False, "max_vnd": 0}}
    out = check_deposit("Không thu tiền ký quỹ", "nhat_ban", policy)
    assert out is not None and out[0] == "PASS"


def test_check_deposit_within_cap():
    policy = {"by_market": {"dai_loan": {"allowed": True, "max_vnd": 10_000_000}}}
    v, _ = check_deposit(9_000_000, "dai_loan", policy)
    assert v == "PASS"
    v, _ = check_deposit(11_000_000, "dai_loan", policy)
    assert v == "FAIL"


def test_blocking_fields_filters_block_only():
    flags = [
        {"code": "A", "field": "tien_luong", "block_field": True, "message": "x"},
        {"code": "B", "field": "khac", "block_field": False},
        {"code": "C", "field": "tien_luong", "block_field": True},  # trùng field -> giữ flag đầu
    ]
    out = blocking_fields(flags)
    assert set(out) == {"tien_luong"} and out["tien_luong"]["code"] == "A"


def test_classify_role_contract_filename():
    rules = load_dossier_rules()
    role = classify_role("2025-11-04_2. Bản sao hợp đồng cung ứng.pdf", "", rules)
    assert role == "hop_dong"
    assert classify_role("khong_lien_quan.pdf", "", rules) == "unknown"


def test_every_required_role_can_actually_be_matched():
    """Vai trò nào nằm trong thành phần BẮT BUỘC thì phải có đường nhận diện.

    `giay_phep` từng có nhãn và nằm trong `required_components.default` nhưng KHÔNG có
    từ khóa và KHÔNG có trong `match_order` — nên nó không bao giờ gán được, và mọi bộ
    hồ sơ ở thị trường mặc định đều bị báo thiếu đúng thành phần đó."""
    rules = load_dossier_rules()
    roles = rules["roles"]
    req = {r for lst in [rules["required_components"]["default"],
                         *rules["required_components"]["by_market"].values()] for r in lst}
    for role in req:
        assert role in roles["match_order"], role
        kw = roles["keywords"].get(role) or {}
        assert kw.get("filename") or kw.get("content"), role


def test_classify_role_receiving_authority_permit():
    """Văn bản của cơ quan có thẩm quyền nước tiếp nhận -> `giay_phep`, không lẫn với
    giấy đăng ký kinh doanh của NSDLĐ (`dkkd_nsdld`) dù nội dung cũng là giấy đăng ký."""
    rules = load_dossier_rules()
    assert classify_role(
        "2025-10-02_3.3. Văn bản của cơ quan có thẩm quyền nước tiếp nhận cho phép người.pdf",
        "", rules) == "giay_phep"
    assert classify_role(
        "2025-10-02_3.4. Giấy phép kinh doanh hoặc đăng ký kinh doanh của tổ chức dịch v.pdf",
        "", rules) == "dkkd_nsdld"


def test_foreign_original_detected_by_diacritic_density():
    """Bản gốc TIẾNG NƯỚC NGOÀI nhận ra bằng mật độ dấu tiếng Việt — không thể dò bằng
    chữ 'bản dịch', vì bản dịch của một giấy tờ Hàn/Nhật chỉ chứa tiếng Việt."""
    from app.domain.compliance.dossier import _is_foreign_text

    assert _is_foreign_text("business registration certificate ship management " * 8)
    assert not _is_foreign_text("Giấy chứng nhận đăng ký kinh doanh quản lý tàu biển " * 8)
    assert not _is_foreign_text("quá ngắn")     # thiếu căn cứ -> không kết luận


# ---------------------------------------------------------------------------
# Đối chiếu chéo THỊ TRƯỜNG (Lớp 4) — khớp theo ranh giới từ, không khớp giữa từ
# ---------------------------------------------------------------------------
def _crosscheck(market_id: str, text: str, country_keywords=None):
    from app.domain.compliance.quality import _market_crosscheck_flags
    from app.store import load_input_quality_config
    return _market_crosscheck_flags(
        market_id, market_id, text, load_input_quality_config(), country_keywords)


def test_market_crosscheck_ignores_substring_matches():
    """'duy nhất', 'thống nhất' KHÔNG được coi là nhắc tới 'nhat_ban'."""
    text = ("Hop dong cung ung lao dong tai Honolulu, Hi 96818, Hoa Ky. "
            "Hai ben thong nhat va cam ket thuc hien duy nhat mot ban hop dong.")
    assert _crosscheck("chau_my", text) == []


def test_market_crosscheck_selected_country_keyword_silences_warning():
    text = "Nguoi lao dong lam viec tren tau ca tai Honolulu, Hawaii."
    assert _crosscheck("chau_my", text, ["honolulu", "hawaii"]) == []


def test_market_crosscheck_warns_on_real_other_market():
    text = ("Hop dong dua thuc tap sinh ky nang sang Nhat Ban lam viec tai Osaka, "
            "Nhat Ban theo chuong trinh cua nghiep doan Nhat Ban.")
    flags = _crosscheck("chau_my", text)
    assert len(flags) == 1 and flags[0]["code"] == "MARKET_MISMATCH"


def test_market_crosscheck_needs_min_hits():
    """Nhắc 1 lần (trích dẫn lẻ) chưa đủ căn cứ -> không cảnh báo."""
    assert _crosscheck("chau_my", "Ap dung tuong tu quy dinh cua thi truong Nhat Ban.") == []


def test_market_keywords_are_not_ambiguous():
    """Từ khóa < 4 ký tự bị bỏ qua khi khớp -> không được để trong cấu hình."""
    from app.store import load_input_quality_config
    kw = (load_input_quality_config().get("market_crosscheck") or {}).get("keywords") or {}
    short = [(m, k) for m, ks in kw.items() for k in ks if len(k.strip()) < 4]
    assert short == []


# ---------------------------------------------------------------------------
# Cấu hình KHU VỰC / QUỐC GIA / LOẠI HÌNH LAO ĐỘNG (trang 1)
# ---------------------------------------------------------------------------
def test_markets_config_has_regions_countries_and_concrete_jobs():
    from app.store import load_markets, resolve_country, resolve_market
    cfg = load_markets()
    region_ids = {r["id"] for r in cfg.get("regions", [])}
    assert region_ids, "Thiếu danh sách khu vực"
    seen_countries: set[str] = set()
    for m in cfg["markets"]:
        # Thị trường KHÔNG quốc gia (vd Biển quốc tế) phải gắn thẳng khu vực qua region_id.
        if not m.get("countries"):
            assert m.get("region_id") in region_ids, (
                f"{m['id']} không có quốc gia thì phải có region_id hợp lệ")
        assert m.get("job_types"), f"{m['id']} chưa có loại hình lao động"
        for c in m["countries"]:
            # KHU VỰC gắn theo TỪNG QUỐC GIA (Đông Bắc Á / Tây Á / Châu Âu...),
            # không gắn theo thị trường: 1 thị trường có thể trải nhiều khu vực.
            assert c.get("region_id") in region_ids, c["id"]
            assert c.get("keywords"), f"{c['id']} thiếu từ khóa đối chiếu"
            assert c["id"] not in seen_countries, f"trùng quốc gia: {c['id']}"
            seen_countries.add(c["id"])
            assert resolve_country(resolve_market(m["id"]), c["id"]) is not None
            # Tên hiển thị: 'English (Tiếng Việt)'
            assert "(" in c["name"] and c["name"].strip().endswith(")"), c["id"]
        # Loại hình phải là NGHỀ CỤ THỂ, không còn nhãn chung chung.
        for t in m["job_types"]:
            assert t["name"].strip().lower() not in {"các ngành, nghề", "các ngành, nghề khác"}


# ---------------------------------------------------------------------------
# B1 — Phát hiện khoản thu trái quy định (3 lớp)
# ---------------------------------------------------------------------------
def _fees(text: str):
    from app.domain.compliance.quality import _prohibited_fee_flags
    from app.store import load_input_quality_config
    return _prohibited_fee_flags(text, load_input_quality_config())


def test_prohibited_fee_blacklist_and_retention():
    codes = {f["code"] for f in _fees("- Tien moi gioi: 30.000.000 VND")}
    assert codes == {"PROHIBITED_FEE"}
    assert {f["code"] for f in _fees("- Cong ty giu ho chieu cua nguoi lao dong")} == {"DOCUMENT_RETENTION"}
    assert _fees("- Tien dich vu: 25.000.000 VND") == []


def test_dieu_khoan_CAM_thu_khong_bi_bao_nguoc_thanh_vi_pham():
    """Hồ sơ nhắc lại điều cấm của Luật 69/2020 là hồ sơ ĐÚNG.

    Hai chỗ hỏng cùng bắn ra một cảnh báo giả trên dòng
    "11.1. … không được thu tiền đặt cọc …":
      · số thứ tự điều khoản "11.1" khớp mẫu SỐ TIỀN cũ `\\d[\\d.,]{2,}` -> dòng đủ
        điều kiện "có số tiền";
      · không có lớp nào nhận ra câu đang PHỦ ĐỊNH -> pattern 'dat coc' trúng.
    """
    cam = ("11.1. Bên tiếp nhận lao động và Bên cung ứng lao động "
           "không được thu tiền đặt cọc liên quan tới hợp đồng")
    assert _fees(cam) == []
    assert _fees("Điều 5. Nghiêm cấm thu tiền môi giới của NLĐ 10.000.000 VND") == []
    # Khoản thu THẬT (câu khẳng định, có số tiền) vẫn phải bắt.
    assert {f["code"] for f in _fees("- Tiền đặt cọc chống trốn: 50.000.000 VND")} \
        == {"PROHIBITED_FEE"}


def test_so_thu_tu_dieu_khoan_khong_phai_so_tien():
    """Mẫu SỐ TIỀN phải đòi nhóm nghìn / >=4 chữ số / có đơn vị tiền."""
    from app.domain.compliance.quality import _HAS_AMOUNT
    for khong_phai in ("11.1.", "2.3", "Điều 5.1", "1.2. Bên A"):
        assert not _HAS_AMOUNT.search(khong_phai), khong_phai
    for la_tien in ("200.000 VND", "25.000.000", "1.500 USD", "500 USD", "200000"):
        assert _HAS_AMOUNT.search(la_tien), la_tien


def test_prohibited_fee_whitelist_scan():
    txt = ("11. Chi phi nguoi lao dong phai tra truoc khi xuat canh:\n"
           "- Tien dich vu: 25.000.000 VND\n"
           "- Phi quan ly ngoai nuoc: 5.000.000 VND\n"
           "- Kham suc khoe: 750.000 VND\n"
           "12. Tien ky quy: 0 VND")
    flags = _fees(txt)
    names = [f["code"] for f in flags]
    assert "PROHIBITED_FEE" in names            # 'phí quản lý ngoài nước' bị cấm
    assert names.count("FEE_NOT_WHITELISTED") == 0   # đã báo ở lớp cấm -> không báo trùng
    assert all("Tien dich vu" not in f["snippet"] for f in flags)


def test_tier_theo_dpi_dung_ba_bac():
    """DPI là NÚT DUY NHẤT: mỗi bậc kéo theo đúng một số ô ảnh Vintern và một `rag_total_cap`.

    Bất biến nằm ở CÁCH CHỌN BẬC, không ở mấy con số: bậc chọn theo `dpi < max_dpi`,
    bậc cuối bao trọn phần còn lại, và bậc cao hơn thì gửi nhiều đoạn luật hơn. Chép
    cứng 8/12/16 vào đây thì mỗi lần chỉnh bảng trong `app/data/settings.json` — việc
    được khuyến khích, `rag_total_cap` là nút chỉnh tải LLM — là test đỏ vì một thay
    đổi hoàn toàn hợp lệ, và người sửa học được rằng cứ sửa số trong test cho xanh."""
    from app.routers.meta import _DPI_TIERS_DEFAULTS, _ocr_cfg, dpi_bounds, tier_for_dpi
    assert dpi_bounds() == (120, 300)

    # Cùng đường lùi như bản chạy thật: thiếu/hỏng settings.json thì dùng bảng mặc định.
    bang = sorted(_ocr_cfg().get("dpi_tiers") or _DPI_TIERS_DEFAULTS,
                  key=lambda t: int(t["max_dpi"]))
    tiles = [int(t["vintern_max_tiles"]) for t in bang]
    assert tiles == sorted(tiles) and len(set(tiles)) == 3, tiles  # bậc cao cắt nhiều ô hơn
    caps = [int(t["rag_total_cap"]) for t in bang]
    assert caps == sorted(caps) and len(set(caps)) == 3, caps   # bậc cao gửi nhiều hơn

    lo1, lo2 = int(bang[0]["max_dpi"]), int(bang[1]["max_dpi"])
    for dpi, bac in ((120, 0), (lo1 - 1, 0), (lo1, 1), (lo2 - 1, 1), (lo2, 2), (300, 2)):
        t = tier_for_dpi(dpi)
        assert t["vintern_max_tiles"] == bang[bac]["vintern_max_tiles"], dpi
        assert t["rag_total_cap"] == bang[bac]["rag_total_cap"], dpi


# ---------------------------------------------------------------------------
# D2 / A3 / E1 — hồi quy các cảnh báo giả từng thấy ở luồng Hàn Quốc · sản xuất chế tạo
# ---------------------------------------------------------------------------
def _doc(name: str, text: str = "") -> dict:
    return {"source_file": name, "ocr_text": text}


_KOREA_MFG_DOCS = [
    _doc("2025-10-17_1. Văn bản đăng ký hợp đồng.pdf",
         "- Ngành, nghê\n"
         "- Đa đim làm vic: 754 Chilbaek-ro, Yeongcheon-si, Gyeongsangbuk-do\n"),
    _doc("2025-10-16_2. Bản sao hợp đồng cung ứng.pdf"),
    _doc("2025-10-16_3.1. Bản sao giấy phép tổ chức có chức năng giới thiệu việc làm kèm .pdf"),
    _doc("2025-10-16_3.2. Bản sao thỏa thuận hợp tác hoặc văn bản yêu cầu hoặc văn bản ủy.pdf"),
    _doc("2025-10-16_3.3. Giấy tờ về người sử dụng lao động thể hiện lĩnh vực kinh doanh .pdf"),
]


def test_nganh_nghe_khong_vo_nham_dong_dia_diem():
    """Ô 'Ngành, nghề' bị OCR bỏ trống thì trả "" — KHÔNG lấy dòng 'Địa điểm làm việc'
    kế bên làm giá trị (nguồn của cảnh báo JOB_TYPE_MISMATCH giả)."""
    assert _nganh_nghe_line("- Ngành, nghê\n- Đa đim làm vic: 754 Chilbaek-ro, Yeongcheon-si\n") == ""
    assert _nganh_nghe_line("Ngành, nghề:\n- Địa điểm làm việc: 754 Chilbaek-ro\n") == ""
    # Nhãn cụt đuôi ("ngh") và nhãn dài kèm dấu hai chấm vẫn phải đọc ra giá trị thật.
    assert _nganh_nghe_line("Ngành, nghề:\nSản xuất chế tạo\n") == "Sản xuất chế tạo"
    assert _nganh_nghe_line("3. Ngành, ngh, công việc: Sản xuất chế tạo linh kiện\n") \
        == "Sản xuất chế tạo linh kiện"


def test_han_quoc_san_xuat_che_tao_dung_bo_ho_so_mac_dinh():
    """Chọn Hàn Quốc + sản xuất chế tạo KHÔNG được đòi hồ sơ nghề thuyền viên.

    `by_market` từng khóa theo cả thị trường 'han_quoc' (di sản thời Hàn Quốc chỉ có
    thuyền viên) nên mọi loại hình khác đều bị đòi xác nhận NFFC + thư ủy quyền chủ tàu."""
    r = analyze_dossier(_KOREA_MFG_DOCS, "han_quoc", None,
                        "Manufacturing (Sản xuất chế tạo)", "san_xuat_che_tao")
    assert r["required_components"] == load_dossier_rules()["required_components"]["default"]
    assert r["missing_components"] == []
    codes = [f["code"] for f in r["flags"]]
    assert "MISSING_COMPONENT" not in codes
    assert "JOB_TYPE_MISMATCH" not in codes
    assert "EVIDENCE_MISSING" not in codes


def test_han_quoc_thuyen_vien_van_giu_bo_ho_so_thuy_san():
    """Khóa hẹp '<thị trường>:<loại hình>' phải giữ nguyên yêu cầu NFFC + ủy quyền chủ tàu."""
    req = analyze_dossier([_doc("2025-10-14_1. Văn bản đăng ký hợp đồng.pdf")], "han_quoc",
                          None, "Thuyền viên tàu cá gần bờ",
                          "thuyen_vien_gan_bo")["required_components"]
    assert "xac_nhan_hiephoi" in req and "uy_quyen_chu_tau" in req


def test_ban_scan_rung_nguyen_am_van_khop_loai_hinh():
    """D2 so bằng KHUNG PHỤ ÂM, nên bản scan rụng nguyên âm vẫn nhận ra đúng loại hình.

    OCR đọc "Lao động đặc định" thành "Lao đng đc đnh"; so chuỗi bỏ dấu thì 'dac
    dinh' không bao giờ trùng 'dc dnh' và D2 kết luận hồ sơ ĐÚNG loại hình là SAI
    loại hình."""
    from app.domain.compliance.dossier import _job_tokens
    doc = "Lao đng đc đnh - Sàn xut máy công nghip, trong đó s có ngh: 1"
    sel = "Specified skilled worker (Lao động kỹ năng đặc định)"
    assert _job_tokens(doc) & _job_tokens(sel), "phải nhận ra là khớp"
    # Bản đọc SẠCH cũng phải khớp (không được sửa chỗ này làm hỏng chỗ kia).
    assert _job_tokens("Lao động đặc định - Sản xuất máy công nghiệp") & _job_tokens(sel)
    # Ngành nghề KHÁC HẲN thì vẫn phải kết luận không khớp.
    assert not (_job_tokens("Thuyền viên tàu cá xa bờ") & _job_tokens(sel))


def test_thoi_han_giay_phep_lay_tu_truong_da_trich_xuat():
    """C2 ưu tiên trường đã trích xuất; mốc neo là dự phòng và nay dò được cả bản rụng dấu.

    Trước: mốc neo "thoi han hieu luc" so NGUYÊN VĂN nên trượt trên bản scan rụng
    nguyên âm ("thi hn hiu lc") -> C2 báo "không xác định được". Nay `marker_pos` so
    thêm bằng KHUNG PHỤ ÂM nên chính bản đó vẫn tìm ra mốc và lấy được ngày sau nó."""
    from datetime import date

    from app.domain.compliance.dossier import _effectivity_flag, _license_validity
    lc = load_dossier_rules()["license_check"]
    scan = "giay phep hoat dong dich vu ... thi hn hiu lc ... 14/02/2025"
    assert _license_validity(scan, lc) == (date(2025, 2, 14), None), "khung phụ âm phải bắt được"
    # Trường đã trích xuất vẫn là nguồn ƯU TIÊN — cùng kết quả, đường chắc chắn hơn.
    assert _effectivity_flag(scan, "2025-03-18", lc, "2025-02-14") is None
    # Nhưng ký TRƯỚC ngày hiệu lực thì vẫn phải báo lỗi.
    assert _effectivity_flag(scan, "2025-01-01", lc, "2025-02-14")["code"] \
        == "LICENSE_EXPIRED_AT_SIGNING"


def test_moc_neo_so_bang_khung_phu_am():
    """Mốc neo rụng nguyên âm vẫn dò ra; mốc quá ngắn KHÔNG được nới (chống trùng giả)."""
    from app.domain.compliance.dossier import has_marker, marker_pos
    folded = "giay phep gioi thieu viec lam ... thi hn hiu lc tu ngay ..."
    assert has_marker(folded, "thoi han hieu luc"), "khung phụ âm 'thhnhlc' phải khớp"
    assert marker_pos(folded, "thoi han hieu luc") == folded.find("thi hn hiu lc")
    assert has_marker(folded, "gioi thieu viec lam"), "bản nguyên văn vẫn phải khớp"
    # Mốc dưới 4 phụ âm chỉ so nguyên văn: 'cho' -> khung 'ch' trùng khắp nơi.
    assert not has_marker(folded, "cho")
    assert not has_marker(folded, "khong co trong van ban nay")


def test_gop_truong_ca_bo_ho_so_lay_gia_tri_dau_tien_doc_duoc():
    """Trường cần cho C2 có thể nằm ở tài liệu bất kỳ — gộp trước rồi tra một lần."""
    from app.domain.documents.intake import merged_fields
    docs = [
        {"contract": {"extracted_fields": {"a": {"value": ""}, "b": {"value": "x"}}}},
        {"contract": {"extracted_fields": {"a": {"value": "y"}, "b": {"value": "z"}}}},
        {"contract": {}},
    ]
    out = merged_fields(docs)
    assert out["a"]["value"] == "y" and out["b"]["value"] == "x"


def test_evidence_missing_khong_bao_khi_da_co_giay_phep():
    """E1 khử trùng theo tài liệu CÓ MẶT, không chỉ theo danh sách bắt buộc."""
    docs = [_doc("1. Văn bản đăng ký hợp đồng.pdf"),
            _doc("3.1. Bản sao giấy phép tổ chức có chức năng giới thiệu việc làm kèm .pdf")]
    msgs = [f["message"] for f in analyze_dossier(docs, "dai_loan", None, "", "khan_ho_cong")["flags"]
            if f["code"] == "EVIDENCE_MISSING"]
    assert not any("tuyển lao động nước ngoài" in m for m in msgs)


# ---------------------------------------------------------------------------
# BỘ TRƯỜNG — bất biến giữa nhóm hiển thị và loại kiểm tra
# ---------------------------------------------------------------------------
def _field_layers():
    """(đường dẫn, fields_catalog) của mọi tầng bộ trường (khu vực/quốc gia/công việc)."""
    from app.store import JOB_LAYER_DIRS
    from app.store.paths import read_json
    for d in JOB_LAYER_DIRS.values():
        for p in sorted(d.glob("*.json")):
            yield p, (read_json(p).get("fields_catalog") or {})


def test_khong_con_truong_khai_bao_nam_trong_nhom_kiem_tra():
    """Trường 'khai báo' nằm ở khối thông tin hồ sơ, KHÔNG lọt vào bảng kiểm tra.

    Lọt vào thì bảng đếm ra một kết luận 'Đã khai báo' mà chú giải không còn giải
    thích — ô đếm và chú giải nói hai bộ nhãn khác nhau."""
    lac = [f"{p.name}:{k}" for p, fc in _field_layers() for k, v in fc.items()
           if isinstance(v, dict) and v.get("field_group") == "check"
           and v.get("check_type") == "declaration"]
    assert not lac, f"Trường khai báo lọt vào nhóm kiểm tra: {lac}"


def test_tang_tren_khong_chep_lai_gia_tri_cua_tang_duoi():
    """Tầng khu vực / quốc gia / công việc chỉ được khai phần KHÁC với tầng dưới.

    Chép lại đúng giá trị của tầng dưới thì hành vi không đổi — và đó chính là vấn đề:
    người đọc không phân biệt được đâu là khác biệt THẬT của thị trường/nghề với đâu là
    bản sao, còn người sửa thì đổi tầng dưới xong tưởng đã xong việc trong khi tầng trên
    vẫn ghì giá trị cũ, im lặng, chỉ ở đúng một nhánh cấu hình.

    Một tầng DÙNG CHUNG (tầng công việc áp cho mọi thị trường) chỉ bị coi là chép thừa
    khi nó trùng tầng dưới ở MỌI bối cảnh nó áp vào. Trùng ở một thị trường mà khác ở
    thị trường khác thì khai lại là bắt buộc — bỏ đi là đổi hành vi."""
    from app.store import load_markets
    from app.store.config import _deep_merge, _layer, region_of

    trung: dict[str, list[bool]] = {}
    for m in load_markets()["markets"]:
        for c in (m.get("countries") or [{"id": ""}]):
            duoi = _layer("regions", region_of(m["id"], c.get("id", ""))) or {}
            for kind, key in (("countries", m["id"]),
                              *(("works", j.get("id")) for j in (m.get("job_types") or []))):
                tren = _layer(kind, key)
                if not tren:
                    continue
                for f, v in (tren.get("fields_catalog") or {}).items():
                    for s, sv in (v or {}).items():
                        same = (duoi.get("fields_catalog") or {}).get(f, {}).get(s) == sv
                        trung.setdefault(f"{kind}/{key}:{f}.{s}", []).append(same)
                duoi = _deep_merge(duoi, tren)
    thua = sorted(k for k, v in trung.items() if all(v))
    assert not thua, f"Tầng trên chép lại {len(thua)} khóa của tầng dưới: {thua[:8]}"


def test_moi_khu_vuc_dung_chung_mot_danh_muc_truong():
    """Nền chung `_base.json` là nguồn DUY NHẤT của 50 trường; khu vực chỉ khai khác biệt.

    Trước 10/08/2026 tám file khu vực chép trọn cùng một danh mục. Bất biến này chặn
    việc quay lại: mọi khu vực phải ra ĐỦ danh mục, và số trường khai riêng ở từng file
    phải nhỏ — file khu vực phình lại là dấu hiệu ai đó vừa chép nguyên nền chung."""
    from app.store.config import BASE_LAYER_FILE, REGIONS_DIR, _layer
    from app.store.paths import read_json

    base = read_json(BASE_LAYER_FILE)["fields_catalog"]
    assert len(base) == 50, "nền chung phải giữ đủ danh mục 50 trường"
    for p in sorted(REGIONS_DIR.glob("*.json")):
        if p.stem == "_base":
            continue
        rieng = read_json(p).get("fields_catalog") or {}
        assert len(rieng) <= 5, f"{p.name} khai riêng {len(rieng)} trường — nghi chép lại nền chung"
        assert len(_layer("regions", p.stem)["fields_catalog"]) == 50, p.name


def test_bao_hiem_va_bao_lanh_la_truong_kiem_tra_co_can_cu():
    """Chế độ bảo hiểm (Điều 19.2.l) và Bảo lãnh (Điều 7.10, Điều 55-59) phải được
    ĐỐI CHIẾU LUẬT, không chỉ ghi nhận: có tiêu chí kiểm tra + có điều luật ở playbook."""
    from app.store import field_check_aspect, load_playbook, resolve_job_prompt
    fc = resolve_job_prompt("nhat_ban", "nhat_ban", "", "nhat_ban")["fields_catalog"]
    pb = load_playbook()["default"]
    for k in ("cac_che_do_bao_hiem", "bao_lanh"):
        assert fc[k]["field_group"] == "check", k
        assert fc[k].get("check_type", "regulated") == "regulated", k
        assert len(field_check_aspect(fc[k], "")) > 80, f"{k} thiếu tiêu chí kiểm tra"
        assert "Luật 69/2020" in pb[k]["law"], f"{k} thiếu điều luật trong playbook"


def test_lop_whitelist_khong_coi_so_thu_tu_dieu_khoan_la_so_tien():
    """`_FEE_LINE` (lớp 3) từng dùng mẫu số tiền LỎNG y hệt `_HAS_AMOUNT` cũ.

    Cùng một lỗi, chỉ khác là lớp này chạy trong vùng đã khoanh nên ít lộ hơn: dòng
    '11.1. Chi phí phát sinh' có '11.1' được đọc thành số tiền, khoản này chui vào
    danh sách 'không thuộc danh mục được phép thu'."""
    from app.domain.compliance.quality import _FEE_LINE
    for khong_phai in ("11.1. Chi phi phat sinh", "2.3 Dieu khoan chung", "1.2. Ben A"):
        assert not _FEE_LINE.match(khong_phai), khong_phai
    for la_khoan_thu in ("Tien dich vu: 25.000.000 VND", "- Phi ho so 3.000.000d",
                         "+ Chi phi khac: 200.000 VND", "Ky quy 500 USD"):
        assert _FEE_LINE.match(la_khoan_thu), la_khoan_thu


def test_moi_vai_tro_tai_lieu_deu_co_nhan_song_ngu():
    """Thiếu `labels_en` thì giao diện tự lùi về bản tiếng Việt — KHÔNG vỡ, nên không
    ai phát hiện; giao diện English chỉ lẫn một dòng tiếng Việt giữa bảng.

    Biến lời nhắc thủ công thành CỔNG CHẶN: thêm vai trò mới mà quên bản English thì
    `npm run test` đỏ ngay, không phải trông vào việc nhớ."""
    roles = load_dossier_rules()["roles"]
    thieu = [r for r in roles["labels"] if not (roles.get("labels_en") or {}).get(r)]
    assert not thieu, f"vai trò thiếu labels_en: {thieu}"
    assert roles.get("unknown_label_en"), "thiếu nhãn English cho vai trò không xác định"


# ---------------------------------------------------------------------------
# C3 — loại giấy phép: chỉ báo sai loại khi KHÔNG có dấu hiệu nào của loại được phép
# ---------------------------------------------------------------------------
def _type_flag(text: str, role: str = "", job_type: str = "Specified skilled worker (Lao động kỹ năng đặc định)"):
    from app.domain.compliance.dossier import _type_flag as f
    return f(text, job_type, load_dossier_rules()["license_check"], role)


def test_giay_phep_gioi_thieu_khong_bi_ket_luan_la_TITP():
    """Markers dò trên TOÀN văn bản, nên một lần nhắc 'thực tập kỹ năng' giữa tài liệu
    đủ bật `titp` lên — và bản sao GIẤY PHÉP GIỚI THIỆU VIỆC LÀM bị báo là giấy phép
    giám sát TITP. Vai trò tài liệu (suy từ tên file + đầu văn bản, tức chỗ ghi TÊN
    giấy phép) là chứng cứ mạnh hơn hẳn."""
    assert _type_flag("giay phep to chuc co chuc nang gioi thieu viec lam ... "
                      "chuong trinh thuc tap ky nang ...", "giay_phep_gioi_thieu") is None
    # Không có vai trò nhưng CÓ dấu hiệu của loại được phép -> cũng không kết luận sai.
    assert _type_flag("giay phep co phi (yu-3) ... nhac toi thuc tap ky nang mot lan") is None


def test_giay_phep_TITP_that_van_bi_bat():
    """Nới để gỡ oan, không phải để tắt kiểm tra: chỉ toàn dấu hiệu TITP thì vẫn báo."""
    titp = "don vi giam sat thuc tap ky nang, doan the giam sat (kanri)"
    assert (_type_flag(titp) or {}).get("code") == "LICENSE_WRONG_TYPE"
    assert (_type_flag(titp, "giay_phep_quan_ly") or {}).get("code") == "LICENSE_WRONG_TYPE"
    # Thực tập sinh thì TITP mới là ĐÚNG loại.
    assert _type_flag(titp, "giay_phep_quan_ly",
                      "Technical intern trainee (Thực tập sinh kỹ năng)") is None


# ---------------------------------------------------------------------------
# Điều 19 — nội dung BẮT BUỘC thiếu: FAIL hay NEEDS_SUPPLEMENT
# ---------------------------------------------------------------------------
def test_thieu_dieu_khoan_kham_chua_benh_la_FAIL_khong_phai_can_bo_sung():
    """Hai chuyện khác hẳn nhau về hệ quả: 'cần bổ sung' là còn sửa được bằng cách nộp
    thêm giấy; còn ở đây chính HỢP ĐỒNG ĐÃ KÝ thiếu điều khoản Điều 19 khoản 2 điểm g
    — đăng ký hợp đồng đó là sai luật."""
    from app.domain.compliance.reconcile import reconcile_checks
    from app.store import resolve_job_prompt
    jp = resolve_job_prompt("nhat_ban", "nhat_ban", "", "nhat_ban")
    res = reconcile_checks({"checks": []}, [], jp, {"extracted_fields": {}}, [])
    by_id = {c["check_id"]: c for c in res["checks"]}
    kcb = by_id["che_do_kham_chua_benh_suc_khoe_sinh_san"]
    assert kcb["verdict"] == "FAIL" and kcb["severity"] == "critical"
    assert "BẮT BUỘC" in kcb["reason"]
    # Trường bắt buộc KHÁC vẫn là NEEDS_SUPPLEMENT — không được nâng cả loạt lên FAIL.
    assert by_id["dia_diem_lam_viec"]["verdict"] == "NEEDS_SUPPLEMENT"


def test_moi_bo_truong_deu_khai_missing_is_fail():
    """Khai thiếu ở một tầng thì thị trường đó lặng lẽ quay về NEEDS_SUPPLEMENT."""
    import json
    import pathlib
    thieu = []
    for p in sorted(pathlib.Path("app/prompts/jobs").rglob("*.json")):
        fcm = json.loads(p.read_text(encoding="utf-8")).get("field_check_mode")
        if isinstance(fcm, dict) and "always_check" in fcm and not fcm.get("missing_is_fail"):
            thieu.append(p.name)
    assert not thieu, f"thiếu missing_is_fail: {thieu}"


def test_fill_hint_bao_hiem_khong_con_goi_y_cau_chung_chung():
    """`check_aspect` coi câu dẫn chiếu chung là CHƯA ĐỦ, mà `fill_hint` lại gợi ý viết
    đúng câu đó — hai chỗ nói ngược nhau thì người nhập làm theo cái nào cũng sai."""
    from app.store import resolve_job_prompt
    hint = resolve_job_prompt("nhat_ban", "nhat_ban", "", "nhat_ban")[
        "fields_catalog"]["cac_che_do_bao_hiem"]["fill_hint"]
    assert "KÊ RÕ TỪNG" in hint
    assert "theo quy định của Nhật Bản'" not in hint or "KHÔNG viết" in hint


# ---------------------------------------------------------------------------
# Biên độ lương — câu cảnh báo phải chịu được biên HỞ MỘT ĐẦU
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lo,hi,mong_doi", [
    (200, 20000, "200–20,000"),
    (None, 20000, "tối đa 20,000"),
    (200, None, "từ 200 trở lên"),
])
def test_bien_do_luong_hien_duoc_ca_khi_ho_mot_dau(lo, hi, mong_doi):
    assert _fmt_band(lo, hi) == mong_doi


def test_bien_do_thieu_mot_dau_van_ra_canh_bao_chu_khong_no():
    """REGRESSION: điều kiện cảnh báo chấp nhận thiếu `min` HOẶC `max` (chặn một phía),
    nên câu thông báo cũng phải chấp nhận. Bản cũ ghép thẳng `f"{lo:,}"` -> `TypeError`
    giữa bước kiểm tra chất lượng đầu vào, và người dùng nhận 500 thay vì một cảnh báo.
    Trang quản trị sửa được `checks.json` mà chỉ kiểm JSON parse được, không kiểm hình
    dạng — biên độ hở một đầu là cấu hình hợp lệ với chính điều kiện vừa chạy qua."""
    contract = {"extracted_fields": {"tien_luong": {"value": {"amount": 999_999, "currency": "USD"}}}}
    cfg = {"salary_range_by_currency": {"USD": {"max": 20000}}}
    flags = _salary_flags(contract, "chau_my", cfg)
    assert [f for f in flags if f["code"] == "SALARY_RANGE"], "phải có cờ SALARY_RANGE"
    assert "tối đa 20,000" in flags[0]["message"]


def test_bien_do_khai_sai_kieu_thi_bo_qua_chu_khong_no():
    """`salary_range_by_currency` có khóa `note` là CHUỖI; khóa nào không phải object
    thì bỏ qua, không được đem `.get` lên chuỗi."""
    contract = {"extracted_fields": {"tien_luong": {"value": {"amount": 999_999, "currency": "NOTE"}}}}
    cfg = {"salary_range_by_currency": {"NOTE": "đây là ghi chú, không phải biên độ"}}
    assert not [f for f in _salary_flags(contract, "chau_my", cfg) if f["code"] == "SALARY_RANGE"]


# ---------------------------------------------------------------------------
# NGÀY KÝ — một chỗ đọc duy nhất cho cả cờ chất lượng lẫn bước dựng báo cáo
# ---------------------------------------------------------------------------
def test_ngay_ky_doc_theo_dung_mot_thu_tu():
    """REGRESSION: trước đây cờ chất lượng đọc trường catalog trước còn bước dựng báo
    cáo bỏ qua nó, nên cùng một hồ sơ màn hình nói một đằng mà bộ lọc quy định lọc một
    nẻo. Thứ tự: trường catalog (người duyệt vừa sửa) -> derived -> contract_meta."""
    assert signed_date_of({
        "extracted_fields": {"ngay_ky_hop_dong": {"value": "2025-03-01"}},
        "derived": {"signed_date": {"value": "2024-01-01"}},
        "contract_meta": {"signed_date": "2023-01-01"},
    }) == "2025-03-01"
    assert signed_date_of({"derived": {"signed_date": {"value": "2024-01-01"}}}) == "2024-01-01"
    assert signed_date_of({"contract_meta": {"signed_date": "2023-01-01"}}) == "2023-01-01"
    assert signed_date_of({}) == "", "không có ngày ký -> chuỗi RỖNG, không bịa mốc"
