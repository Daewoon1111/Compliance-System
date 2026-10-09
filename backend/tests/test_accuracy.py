"""Test CHỈ SỐ CHẤT LƯỢNG ĐỌC của một bộ hồ sơ (CER, WER, độ chính xác trường…).

Đáp án = giá trị sau khi người duyệt soát; giá trị máy = bản gốc lưu ở `machine` khi
người duyệt sửa lần đầu (xem PATCH .../fields).
"""
from __future__ import annotations

import pytest

from app.domain.compliance.accuracy import edit_distance, run_accuracy, value_text
from app.store import audit as audit_store


def _f(value, vt="text", machine=...):
    e = {"value": value, "value_type": vt}
    if machine is not ...:
        e["machine"] = {"value": machine}
    return e


def _docs(**fields):
    return [{"contract": {"extracted_fields": fields}}]


def test_edit_distance():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
    assert edit_distance(["a", "b"], ["a", "c", "b"]) == 1


@pytest.mark.parametrize("v,expect", [
    (None, ""), ("  Hà   Nội ", "Hà Nội"), (3.0, "3"), (7, "7"),
    ({"amount": 120000000.0, "currency": "VND"}, "120000000 VND"),
    ({"raw": "Không thu"}, "Không thu"),
])
def test_value_text(v, expect):
    assert value_text(v) == expect


def test_khong_sua_gi_la_dung_het():
    acc = run_accuracy(_docs(a=_f("Công ty A"), b=_f("2025-03-01", "date"), c=_f(None)))
    assert acc["field_accuracy"] == 1.0 and acc["cer"] == 0.0 and acc["wer"] == 0.0
    assert acc["ocr_accuracy"] == 1.0 and acc["fields"] == 2       # trường trống không tính
    assert acc["table_accuracy"] is None and acc["number_accuracy"] is None
    assert acc["date_accuracy"] == 1.0


def test_tinh_dung_tren_truong_da_sua():
    docs = _docs(
        ten=_f("Công ty Alpha", machine="Cong ty Alpha"),                 # 1 ký tự, 1 từ sai
        gia=_f({"amount": 120_000_000}, "money", machine={"amount": 12_000_000}),
        ngay=_f("2025-03-01", "date", machine=None),                       # máy bỏ sót
        so=_f(3, "number"),
        bang=_f("Mục 1", machine="Muc 1"),
    )
    acc = run_accuracy(docs, {"bang": {"in_table": True}})
    assert acc["fields"] == 5 and acc["fields_correct"] == 1 and acc["fields_edited"] == 4
    assert acc["field_accuracy"] == 0.2
    assert acc["number_accuracy"] == 0.5          # tiền sai, số đúng
    assert acc["date_accuracy"] == 0.0
    assert acc["table_accuracy"] == 0.0
    ref_chars = len("Công ty Alpha") + len("120000000") + len("2025-03-01") + 1 + len("Mục 1")
    assert acc["chars"] == ref_chars
    assert acc["cer"] == round((1 + 1 + 10 + 0 + 1) / ref_chars, 4)
    assert acc["ocr_accuracy"] == round(1 - acc["cer"], 4)
    # Từ: "Cong"≠"Công" (1), "12000000"≠ (1), thiếu ngày (1), "Muc" (1) trên 3+1+1+1+2 từ.
    assert acc["wer"] == round(4 / 8, 4)


def test_thong_ke_trung_binh_theo_bo_truong_va_tung_bo_ho_so(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    for i, fa in enumerate((1.0, 0.5)):
        audit_store.record_run(f"s{i}", "fs", "Hợp đồng", [], source_files=[f"{i}.pdf"],
                               accuracy={"cer": 0.1 * i, "wer": None, "field_accuracy": fa})
    audit_store.record_run("s9", "fs", "Hợp đồng", [])          # bản ghi cũ: không có chỉ số
    st = audit_store.aggregate_stats()
    m = st["by_field_set"]["Hợp đồng"]["accuracy"]
    assert m["runs"] == 2 and m["field_accuracy"] == 0.75 and m["cer"] == 0.05
    assert m["wer"] is None and m["date_accuracy"] is None
    assert [r["session_id"] for r in st["runs"]] in (["s1", "s0"], ["s0", "s1"])
    assert st["accuracy"]["field_accuracy"] == 0.75
