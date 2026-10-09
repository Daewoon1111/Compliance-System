"""Test ĐO THẬT (eval) — recall@k, độ chính xác trích xuất và trích dẫn trên hồ sơ có nhãn.

Đây là phần đo cho ra CON SỐ ĐI VÀO BÁO CÁO, nên chính nó phải được kiểm kỹ nhất: một
phép so khớp quá rộng làm mọi tỉ lệ đẹp lên mà không ai biết, và số đẹp sai còn tệ hơn
không có số.
"""
from __future__ import annotations

import json

import pytest

from app import eval as ev
from app.core import settings


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(ev, "GOLDEN_DIR", tmp_path / "golden")


def _session(sid: str, fields: dict, detail: dict | None = None, checks: list | None = None):
    base = ev.Path(settings.temp_dir) / sid
    base.mkdir(parents=True, exist_ok=True)
    (base / "documents.json").write_text(json.dumps({"documents": [
        {"contract": {"extracted_fields": {k: {"value": v} for k, v in fields.items()}}},
    ]}, ensure_ascii=False), encoding="utf-8")
    (base / "final_report.json").write_text(json.dumps({
        "documents": [{"checks": checks or []}],
        "metrics": {"retrieval": {"detail": detail or {}}},
    }, ensure_ascii=False), encoding="utf-8")


def _case(**kw):
    ev.GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    (ev.GOLDEN_DIR / f"{kw['case_id']}.json").write_text(
        json.dumps(kw, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# So khớp giá trị
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("nhan", "he"), [
    ("1 năm", "1 nam"),                       # nhãn có dấu, hệ trả bản đã chuẩn hóa
    ("184.461", "184461"),                    # dấu phân cách nghìn kiểu Việt Nam
    ("184461", 184461),                       # số nguyên và chuỗi số là một
    ("Trên các tàu", "trên các tàu ABC"),      # nhãn điều khoản dài chỉ chép được một đoạn
    (None, ""),                                # cùng rỗng
    (None, None),
])
def test_khop_gia_tri(nhan, he):
    assert ev.values_match(nhan, he)


@pytest.mark.parametrize(("nhan", "he"), [
    ("1 năm", "3 năm"),
    ("184461", "184462"),
    (None, "có giá trị"),   # nhãn nói KHÔNG CÓ mà hệ vẫn điền -> đúng lỗi 'bắt bừa'
    ("1 năm", None),
])
def test_khong_khop_gia_tri(nhan, he):
    assert not ev.values_match(nhan, he)


def test_chuan_hoa_khong_tu_sua_ho_nguoi_dan_nhan():
    """Chuẩn hóa càng mạnh thì số càng đẹp và càng xa sự thật — chỉ bỏ dấu, thường hóa,
    gộp khoảng trắng và cắt dấu câu ở hai đầu."""
    assert ev.normalize("  Đúng, HAI  chỗ. ") == "dung, hai cho"
    assert ev.normalize(None) == ""


# ---------------------------------------------------------------------------
# Ba phép đo
# ---------------------------------------------------------------------------
def test_do_chinh_xac_trich_xuat_dem_ca_ca_bat_bua():
    case = {"fields": {"a": "1 năm", "b": None, "c": "x"}}
    fields = {"a": "1 nam", "b": "bịa ra", "c": "x"}
    out = ev.extraction_accuracy(case, fields)
    assert out == {"total": 3, "correct": 2, "accuracy": round(2 / 3, 3),
                   "wrong": [{"field": "b", "expected": None, "actual": "bịa ra"}]}


def test_recall_at_k_dem_tren_doan_luat_dung():
    """Khác hẳn phủ truy hồi: phủ nói 'có kéo về đoạn nào đó', recall nói 'đúng đoạn cần'."""
    case = {"relevant_chunks": {"f1": ["c1", "c2"], "f2": ["c9"]}}
    report = {"metrics": {"retrieval": {"detail": {"f1": ["c1", "c7"], "f2": []}}}}
    out = ev.recall_at_k(case, report)
    assert out["relevant"] == 3 and out["retrieved"] == 1
    assert out["recall"] == round(1 / 3, 3)
    assert {d["field"]: d["missed"] for d in out["detail"]} == {"f1": ["c2"], "f2": ["c9"]}


def test_do_chinh_xac_trich_dan_so_tren_ten_van_ban():
    """So trên TÊN văn bản, không trên mã đoạn: mã đoạn gồm hàm băm nội dung nên đổi
    mỗi lần seed lại, còn 'Thông tư mẫu số 05/2024' thì không."""
    case = {"citations": {"k1": ["Thông tư mẫu số 05/2024"], "k2": ["Quy định mẫu về hợp đồng dịch vụ"]}}
    report = {"documents": [{"checks": [
        {"check_id": "k1", "citations": [{"source_doc": "Thông tư mẫu số 05/2024"}]},
        {"check_id": "k2", "citations": [{"source_doc": "Nghị định mẫu số 10/2021"}]},
    ]}]}
    out = ev.citation_accuracy(case, report)
    assert out["total"] == 2 and out["correct"] == 1 and out["accuracy"] == 0.5
    assert out["misses"][0]["check_id"] == "k2"


# ---------------------------------------------------------------------------
# Nạp nhãn + gộp số
# ---------------------------------------------------------------------------
def test_thieu_artefact_phien_thi_bao_ro_chu_khong_do_bua():
    _case(case_id="G01", session_id="khong-co", fields={"a": "1"})
    r = ev.evaluate_case(ev.load_cases()[0])
    assert r["ok"] is False and "khong-co" in r["error"]


def test_gia_tri_lay_theo_tai_lieu_dau_tien_doc_duoc():
    """Đúng bằng quy tắc gộp của `merge_contracts` — chỉ bù trường còn TRỐNG."""
    docs = {"documents": [
        {"contract": {"extracted_fields": {"a": {"value": ""}, "b": {"value": "x"}}}},
        {"contract": {"extracted_fields": {"a": {"value": "y"}, "b": {"value": "z"}}}},
    ]}
    assert ev._merged_fields(docs) == {"a": "y", "b": "x"}


def test_nhan_hong_khong_chan_ca_luot_do():
    ev.GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    (ev.GOLDEN_DIR / "hong.json").write_text("{ khong phai json", encoding="utf-8")
    _case(case_id="G01", session_id="s1", fields={"a": "1"})
    _session("s1", {"a": "1"})
    assert [c["case_id"] for c in ev.load_cases()] == ["G01"]


def test_gop_theo_tong_so_truong_khong_phai_trung_binh_cua_ti_le():
    """Hồ sơ dán nhãn 3 trường và hồ sơ dán nhãn 40 trường không được cùng trọng số."""
    _case(case_id="G01", session_id="s1", fields={"a": "1"},
          relevant_chunks={"f": ["c1"]}, citations={"k": ["L"]})
    _case(case_id="G02", session_id="s2", fields={"a": "1", "b": "2", "c": "3"})
    _session("s1", {"a": "1"}, detail={"f": ["c1"]},
             checks=[{"check_id": "k", "citations": [{"source_doc": "L"}]}])
    _session("s2", {"a": "1", "b": "sai", "c": "sai"})
    s = ev.run_eval()["summary"]
    assert s["cases_measured"] == 2 and s["extraction_fields"] == 4
    assert s["extraction_accuracy"] == 0.5          # 2/4, KHÔNG phải (1,0 + 0,333)/2
    assert s["recall_at_k"] == 1.0 and s["citation_accuracy"] == 1.0


def test_chua_co_nhan_thi_noi_thang_la_chua_co():
    """Trả 0% khi chưa đo gì là một khẳng định sai — phải là None kèm lời nhắc."""
    res = ev.run_eval()
    assert res["summary"]["cases_total"] == 0
    assert res["summary"]["extraction_accuracy"] is None
    assert res["note"]


def test_chep_nham_artefact_vao_golden_thi_bao_dung_benh():
    """Nhầm lẫn tự nhiên nhất: chép cả thư mục `temp/<phiên>` vào `golden/`.

    Câu 'chưa có hồ sơ nào có nhãn' khi đó đúng chữ nhưng sai bệnh — người đọc đi dán
    thêm nhãn trong khi việc cần làm là bỏ artefact ra khỏi thư mục nhãn."""
    nham = ev.GOLDEN_DIR / "60701857-1bc2-4d20-9690-6a5dc94c6210"
    nham.mkdir(parents=True)
    (nham / "documents.json").write_text("{}", encoding="utf-8")

    note = ev.run_eval()["note"]
    assert "ARTEFACT" in note and nham.name in note
    assert "session_id" in note, "phải nói artefact được nạp qua khóa nào"


def test_nhan_sai_cu_phap_duoc_goi_ten_chu_khong_bien_mat():
    """Nhãn hỏng bị bỏ qua để không chặn cả lượt đo — nhưng bỏ qua trong IM LẶNG thì
    số đo tụt mà không ai biết vì sao. Tên tệp hỏng phải đi vào báo cáo."""
    _case(case_id="G01", session_id="s1", fields={"a": "1"})
    _session("s1", {"a": "1"})
    (ev.GOLDEN_DIR / "G02.json").write_text("{ hỏng", encoding="utf-8")

    res = ev.run_eval()
    assert res["summary"]["cases_total"] == 1, "nhãn hỏng không được chặn hồ sơ còn lại"
    assert res["bad_labels"] and "G02.json" in res["bad_labels"][0]
    assert "G02.json" in res["note"]


def test_nhan_chua_xac_nhan_bi_goi_ten_trong_bao_cao(capsys):
    """Nhãn dựng sẵn từ chính đầu ra của hệ cho độ chính xác luôn bằng 1.0 — chuẩn và
    bài thi là cùng một thứ. Số đó vẫn bắt được hồi quy, nhưng đi vào báo cáo mà không
    kèm cảnh báo thì nó là một lời khẳng định sai."""
    _case(case_id="G01", session_id="s1", fields={"a": "1"})       # thiếu verified
    _session("s1", {"a": "1"})
    res = ev.run_eval()
    assert res["summary"]["extraction_accuracy"] == 1.0
    assert res["summary"]["cases_unverified"] == 1
    assert "CHƯA ĐƯỢC NGƯỜI XÁC NHẬN" in res["note"] and "G01" in res["note"]
    ev.print_report(res)
    assert "CHƯA XÁC NHẬN" in capsys.readouterr().out

    _case(case_id="G01", session_id="s1", fields={"a": "1"}, verified=True)
    res = ev.run_eval()
    assert res["summary"]["cases_unverified"] == 0 and not res["note"]


def test_in_bao_cao_khong_no_khi_co_va_khong_co_nhan(capsys):
    ev.print_report(ev.run_eval())
    assert "Chưa có hồ sơ nào có nhãn" in capsys.readouterr().out

    _case(case_id="G01", session_id="s1", fields={"a": "1", "b": "2"})
    _session("s1", {"a": "1", "b": "khac"})
    ev.print_report(ev.run_eval())
    out = capsys.readouterr().out
    assert "Độ chính xác trích xuất" in out and "SAI b" in out
