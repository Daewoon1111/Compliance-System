"""Test hợp nhất kết quả kiểm tra: verdict, gộp contract, reconcile, trích dẫn."""
from app.domain.compliance.payload import VALIDATION_SCHEMA
from app.domain.compliance.reconcile import (
    backfill_citations,
    best_chunk_for,
    completeness,
    field_check_type,
    field_group,
    has_value,
    merge_contracts,
    overall_verdict,
    reconcile_checks,
)
from app.domain.regulations import _where_clause, date_to_int
from app.store import load_field_set

_DOAN = {
    "id": "c1",
    "text": ("Điều 5. Thời hạn hợp đồng dịch vụ phải được xác định rõ ngày bắt đầu "
             "và ngày kết thúc thực hiện."),
    "metadata": {"source_doc": "Quy định mẫu về hợp đồng dịch vụ", "doc_type": "regulation",
                 "effective_from": "2020-01-01", "effective_to": "9999-12-31"},
}


# ---------------------------------------------------------------------------
# Verdict + tiện ích
# ---------------------------------------------------------------------------
def test_overall_verdict_priority():
    assert overall_verdict(["PASS", "FAIL", "NEEDS_SUPPLEMENT"]) == "FAIL"
    assert overall_verdict(["FAIL", "NEEDS_SUPPLEMENT"]) == "FAIL"
    assert overall_verdict(["PASS", "NEEDS_SUPPLEMENT"]) == "NEEDS_SUPPLEMENT"
    assert overall_verdict(["PASS", "DECLARATION"]) == "PASS"   # khai báo KHÔNG chặn
    assert overall_verdict([]) == "PASS"


def test_has_value():
    assert has_value(0) is True                       # 0 là giá trị hợp lệ
    assert has_value("") is False and has_value("  ") is False and has_value(None) is False
    # Object tiền VỎ RỖNG (mô hình trả khung mà không có số) -> trống.
    assert has_value({"amount": None, "currency": "VND", "raw": ""}) is False
    assert has_value({"amount": None, "raw": "một trăm triệu"}) is True
    assert has_value({"amount": 0, "currency": "VND"}) is True


def test_check_type_va_nhom_hien_thi():
    assert field_check_type({"check_type": "declaration"}) == "declaration"
    assert field_check_type({}) == "regulated"            # mặc định đối chiếu quy định
    assert field_check_type("nhãn trần") == "regulated"
    assert field_group({"check_type": "declaration"}) == "declaration"
    assert field_group({"check_type": "positive_integer"}) == "check"


def test_completeness_counts_missing_required():
    jp = {"field_check_mode": {"always_check": ["a", "b"]}}
    cj = {"extracted_fields": {"a": {"value": "x"}, "b": {"value": None}}}
    out = completeness(jp, cj)
    assert out == {"required_total": 2, "required_missing": ["b"], "is_complete": False}


# ---------------------------------------------------------------------------
# Gộp nhiều tài liệu
# ---------------------------------------------------------------------------
def test_merge_contracts_fills_missing_from_second():
    c1 = {"extracted_fields": {"a": {"value": None}, "b": {"value": "giữ"}},
          "input_flags": [{"code": "X", "message": "m"}]}
    c2 = {"extracted_fields": {"a": {"value": "bù"}, "b": {"value": "đè?"}, "c": {"value": "thêm"}},
          "input_flags": [{"code": "X", "message": "m"}, {"code": "Y", "message": "n"}]}
    out = merge_contracts([c1, c2])
    ef = out["extracted_fields"]
    assert ef["a"]["value"] == "bù" and ef["b"]["value"] == "giữ" and ef["c"]["value"] == "thêm"
    assert [f["code"] for f in out["input_flags"]] == ["X", "Y"]  # khử trùng lặp
    assert out["missing_fields"] == []
    assert c1["extracted_fields"]["a"]["value"] is None, "không được sửa contract gốc"


def test_merge_contracts_bo_canh_bao_thieu_ngay_ky_khi_tai_lieu_sau_co():
    meta = {"signed_date_field": "ngay_ky"}
    c1 = {"contract_meta": meta, "extracted_fields": {"ngay_ky": {"value": None}},
          "input_flags": [{"code": "SIGNED_DATE_MISSING", "message": "thiếu"}]}
    c2 = {"contract_meta": meta, "extracted_fields": {"ngay_ky": {"value": "2025-05-05"}}}
    out = merge_contracts([c1, c2])
    assert out["extracted_fields"]["ngay_ky"]["value"] == "2025-05-05"
    assert out["input_flags"] == []
    # Không tài liệu nào có ngày ký -> cảnh báo phải giữ nguyên.
    still = merge_contracts([c1, {"contract_meta": meta, "extracted_fields": {}}])
    assert [f["code"] for f in still["input_flags"]] == ["SIGNED_DATE_MISSING"]
    assert still["missing_fields"] == ["ngay_ky"]


def test_merge_contracts_rong():
    assert merge_contracts([]) == {"extracted_fields": {}, "input_flags": [],
                                   "missing_fields": []}


# ---------------------------------------------------------------------------
# reconcile_checks
# ---------------------------------------------------------------------------
def _job_prompt():
    return {"fields_catalog": {
        "khai_bao": {"label": "Khai báo", "check_type": "declaration"},
        "so_luong": {"label": "Số lượng", "check_type": "positive_integer"},
        "quy_dinh": {"label": "Theo quy định", "check_type": "regulated"},
    }}


def test_reconcile_declaration_and_deterministic_no_llm():
    contract = {"extracted_fields": {
        "khai_bao": {"value": "có"}, "so_luong": {"value": 5}, "quy_dinh": {"value": "x"},
    }}
    # Mô hình không trả check nào -> khai báo/tất định vẫn có kết luận, regulated -> cần bổ sung.
    out = reconcile_checks({"checks": []}, ["khai_bao", "so_luong", "quy_dinh"],
                           _job_prompt(), contract)
    by = {c["check_id"]: c for c in out["checks"]}
    assert by["khai_bao"]["verdict"] == "DECLARATION"
    assert by["so_luong"]["verdict"] == "PASS" and by["so_luong"]["deterministic"] is True
    assert by["quy_dinh"]["verdict"] == "NEEDS_SUPPLEMENT"
    assert out["overall_verdict"] == "NEEDS_SUPPLEMENT"


def test_positive_integer_fail_khong_can_trich_dan():
    """Kết luận tất định có căn cứ là chính tiêu chí của bộ trường — không bị hạ vì
    thiếu đoạn quy định."""
    contract = {"extracted_fields": {"so_luong": {"value": 0}}}
    out = reconcile_checks({"checks": []}, ["so_luong"], _job_prompt(), contract, [])
    c = out["checks"][0]
    assert c["verdict"] == "FAIL" and c["severity"] == "medium"
    assert out["overall_verdict"] == "FAIL" and out["violations_summary"] == [c]


def test_khong_tin_title_va_value_cua_mo_hinh():
    contract = {"extracted_fields": {"quy_dinh": {"value": "giá trị thật"}}}
    llm = {"checks": [
        {"check_id": "quy_dinh", "title": "BỊA (Quy định::3::abcdef123456)",
         "field_value": "BỊA", "verdict": "NEEDS_SUPPLEMENT",
         "reason": "Chưa rõ (Quy định mẫu::3::abcdef123456) cần xem lại"},
        {"check_id": "quy_dinh", "verdict": "PASS", "reason": "bản trùng bị bỏ"},
    ]}
    out = reconcile_checks(llm, ["quy_dinh"], _job_prompt(), contract)
    c = out["checks"][0]
    assert len(out["checks"]) == 1
    assert c["title"] == "Theo quy định" and c["field_value"] == "giá trị thật"
    assert c["reason"] == "Chưa rõ cần xem lại", "mã đoạn nội bộ phải bị lọc khỏi lời giải thích"


def test_reconcile_blocking_flag_downgrades():
    contract = {"extracted_fields": {"quy_dinh": {"value": "x"}},
                "input_flags": [{"code": "OCR_BLURRY", "field": "quy_dinh",
                                 "block_field": True, "message": "bản scan mờ"}]}
    llm = {"checks": [{"check_id": "quy_dinh", "verdict": "PASS", "reason": "ok",
                       "citations": [{"chunk_id": "c1"}]}]}
    out = reconcile_checks(llm, ["quy_dinh"], _job_prompt(), contract, [_DOAN])
    c = out["checks"][0]
    assert c["verdict"] == "NEEDS_SUPPLEMENT" and c["input_quality_flag"] == "OCR_BLURRY"
    assert "bản scan mờ" in c["reason"]


def _jp_bat_buoc():
    return {
        "fields_catalog": {
            "thoi_han": {"label": "Thời hạn", "check_type": "regulated"},
            "tranh_chap": {"label": "Giải quyết tranh chấp", "check_type": "regulated"},
            "dieu_khoan_bat_buoc": {"label": "Điều khoản bắt buộc", "check_type": "regulated"},
            "email": {"label": "Email liên hệ", "check_type": "declaration"},
            "dien_thoai": {"label": "Điện thoại liên hệ", "check_type": "declaration"},
        },
        "field_check_mode": {
            "always_check": ["thoi_han", "tranh_chap", "dieu_khoan_bat_buoc"],
            "missing_is_fail": ["dieu_khoan_bat_buoc"],
            "required_one_of": [["email", "dien_thoai"]],
        },
    }


def test_truong_bat_buoc_trong_va_required_one_of():
    contract = {"extracted_fields": {"thoi_han": {"value": "12 tháng"}}}
    llm = {"checks": [{"check_id": "thoi_han", "verdict": "PASS", "reason": "ok",
                       "citations": [{"chunk_id": "c1"}]}]}
    rag = [{**_DOAN, "field_ranks": {"thoi_han": 0}}]
    out = reconcile_checks(llm, ["thoi_han"], _jp_bat_buoc(), contract, rag)
    by = {c["check_id"]: c for c in out["checks"]}
    assert by["thoi_han"]["verdict"] == "PASS"
    # Trường bắt buộc trống -> cần bổ sung ...
    assert by["tranh_chap"]["verdict"] == "NEEDS_SUPPLEMENT"
    # ... trừ khi bộ trường khai thiếu nội dung đó là vi phạm (`missing_is_fail`).
    kq = by["dieu_khoan_bat_buoc"]
    assert kq["verdict"] == "FAIL" and kq["severity"] == "critical" and kq["mandatory_missing"]
    assert "BẮT BUỘC" in kq["reason"]
    # Nhóm "ít nhất một" trống cả nhóm -> một dòng cần bổ sung.
    one = by["one_of_email"]
    assert one["verdict"] == "NEEDS_SUPPLEMENT" and one["missing_fields"] == ["email", "dien_thoai"]
    assert out["overall_verdict"] == "FAIL", "FAIL do thiếu nội dung bắt buộc không bị hạ"


def test_required_one_of_du_khi_mot_ben_co_gia_tri():
    contract = {"extracted_fields": {
        "thoi_han": {"value": "12 tháng"}, "tranh_chap": {"value": "Tòa án"},
        "dieu_khoan_bat_buoc": {"value": "có"}, "dien_thoai": {"value": "0123"},
    }}
    keys = ["thoi_han", "tranh_chap", "dieu_khoan_bat_buoc"]
    llm = {"checks": [{"check_id": k, "verdict": "PASS", "reason": "ok"} for k in keys]}
    rag = [{**_DOAN, "field_ranks": dict.fromkeys(keys, 0)}]
    out = reconcile_checks(llm, keys, _jp_bat_buoc(), contract, rag)
    assert not any(c["check_id"].startswith("one_of_") for c in out["checks"])
    assert out["overall_verdict"] == "PASS"


def test_pass_va_fail_khong_can_cu_bi_ha():
    """PASS/FAIL của mô hình mà không gắn được đoạn quy định nào -> cần bổ sung."""
    jp = {"fields_catalog": {"a": {"label": "Mục A"}, "b": {"label": "Mục B"}}}
    contract = {"extracted_fields": {
        "a": {"value": "x"},
        "b": {"value": "y", "evidence": {"short_quote": "trích đoạn hồ sơ"}},
    }}
    llm = {"checks": [{"check_id": "a", "verdict": "PASS", "reason": "hợp lệ"},
                      {"check_id": "b", "verdict": "FAIL", "reason": "sai", "citations": []}]}
    out = reconcile_checks(llm, ["a", "b"], jp, contract, [])
    by = {c["check_id"]: c for c in out["checks"]}
    for k in ("a", "b"):
        assert by[k]["verdict"] == "NEEDS_SUPPLEMENT" and "Thiếu căn cứ" in by[k]["reason"], k
    assert by["b"]["contract_quote"] == "trích đoạn hồ sơ"
    assert out["violations_summary"] == []


# ---------------------------------------------------------------------------
# Trích dẫn: mọi kết luận PASS/FAIL phải có căn cứ
# ---------------------------------------------------------------------------
def test_backfill_gan_doan_cua_dung_truong_hoac_do_toan_ro():
    rag_rieng = [{**_DOAN, "field_ranks": {"thoi_han": 0}}]
    checks = [
        {"check_id": "thoi_han", "title": "Thời hạn", "verdict": "PASS", "citations": []},
        {"check_id": "x", "title": "X", "verdict": "NEEDS_SUPPLEMENT", "citations": []},
    ]
    out = backfill_citations(checks, {}, rag_rieng)
    assert out[0]["citations"][0]["chunk_id"] == "c1"
    assert out[0]["citations"][0]["auto_matched"] is True
    assert out[1]["citations"] == []      # NEEDS_SUPPLEMENT không cần trích dẫn

    # Không có đoạn riêng của trường -> dò toàn rổ theo từ chung với tiêu chí kiểm tra.
    fc = {"thoi_han": {"label": "Thời hạn",
                       "check_aspect": "Thời hạn hợp đồng dịch vụ phải xác định rõ."}}
    out = backfill_citations([{"check_id": "thoi_han", "title": "Thời hạn",
                               "verdict": "FAIL", "citations": []}], fc, [_DOAN])
    assert out[0]["citations"][0]["source_doc"] == "Quy định mẫu về hợp đồng dịch vụ"


def test_backfill_chi_giu_doan_cua_dung_truong():
    """Đoạn quy định RAG kéo về cho trường khác bị loại khỏi trích dẫn."""
    rag = [
        {"id": "c_han", "text": "Điều 5. Thời hạn…", "field_ranks": {"thoi_han": 0},
         "metadata": {"source_doc": "Quy định mẫu"}},
        {"id": "c_tt", "text": "Điều 7. Thanh toán…", "field_ranks": {"thanh_toan": 0},
         "metadata": {"source_doc": "Quy định mẫu"}},
    ]
    checks = [{"check_id": "thoi_han", "verdict": "PASS", "reason": "ok",
               "citations": [{"chunk_id": "c_tt"}]}]
    out = backfill_citations(checks, {}, rag)
    assert [ct["chunk_id"] for ct in out[0]["citations"]] == ["c_han"]


def test_backfill_bo_trich_dan_khong_co_trong_ro():
    """Trường không có đoạn riêng: trích dẫn mô hình tự chọn vẫn phải nằm trong rổ đã gửi."""
    checks = [{"check_id": "k", "verdict": "PASS", "reason": "",
               "citations": [{"chunk_id": "khong-ton-tai"}]}]
    out = backfill_citations(checks, {}, [{**_DOAN, "text": "nội dung không liên quan"}])
    assert out[0]["citations"] == []


def test_best_chunk_can_it_nhat_hai_tu_chung():
    assert best_chunk_for("thời hạn hợp đồng", [_DOAN]) is _DOAN
    assert best_chunk_for("thanh toán", [_DOAN]) is None
    assert best_chunk_for("", [_DOAN]) is None


def test_trich_dan_dung_lai_tu_chunk_goc_chu_khong_tin_chu_model_chep():
    """Mô hình CHỈ trả `chunk_id`; tên văn bản, hiệu lực, nguyên văn do backend điền."""
    cit_props = (VALIDATION_SCHEMA["properties"]["checks"]["items"]["properties"]
                 ["citations"]["items"]["properties"])
    assert set(cit_props) == {"chunk_id"}, cit_props

    rag = [{**_DOAN, "field_ranks": {"thoi_han": 0}}]
    checks = [{"check_id": "thoi_han", "verdict": "PASS", "reason": "ok",
               "citations": [{"chunk_id": "c1", "source_doc": "BỊA", "text_quote": "BỊA"}]}]
    ct = backfill_citations(checks, {}, rag)[0]["citations"][0]
    assert ct["chunk_id"] == "c1" and ct["auto_matched"] is False
    assert ct["source_doc"] == "Quy định mẫu về hợp đồng dịch vụ"
    assert ct["text_quote"].startswith("Điều 5.") and "BỊA" not in ct["text_quote"]
    assert ct["effective_from"] == "2020-01-01"


# ---------------------------------------------------------------------------
# Bộ trường mặc định chạy trọn reconcile
# ---------------------------------------------------------------------------
def test_bo_truong_mau_ho_so_trong():
    fs = load_field_set("hop_dong_mau")
    out = reconcile_checks({"checks": []}, [], fs, {"extracted_fields": {}}, [])
    ids = {c["check_id"] for c in out["checks"]}
    assert ids == set(fs["field_check_mode"]["always_check"])
    assert {c["verdict"] for c in out["checks"]} == {"NEEDS_SUPPLEMENT"}
    assert out["overall_verdict"] == "NEEDS_SUPPLEMENT"


# ---------------------------------------------------------------------------
# Bộ lọc hiệu lực theo NGÀY KÝ
# ---------------------------------------------------------------------------
def test_where_clause_loc_theo_ngay_ky():
    signed = date_to_int("2025-12-11")
    assert signed == 20251211                       # dạng số so sánh được của ChromaDB
    w = _where_clause("2025-12-11", "VN", ["decree", "circular"])
    conds = {next(iter(c)): c[next(iter(c))] for c in w["$and"]}
    assert conds == {
        "jurisdiction": {"$eq": "VN"},
        "doc_type": {"$in": ["decree", "circular"]},
        "effective_from_int": {"$lte": signed},
        "effective_to_int": {"$gte": signed},
    }


def test_date_to_int_chiu_dau_vao_xau():
    assert date_to_int("2022-01-01") == 20220101
    assert date_to_int(None, default=19000101) == 19000101
    assert date_to_int("không phải ngày", default=0) == 0
