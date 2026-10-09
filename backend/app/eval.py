"""ĐO THẬT (eval) — GOLDEN CORPUS: recall@k và độ chính xác trích xuất trên bộ hồ sơ có NHÃN.

Vì sao module này tồn tại: `metrics.retrieval_quality` chỉ đo được PHỦ truy hồi — mỗi
trường có kéo về đoạn quy định nào không. Nó bắt được ca RAG trả rỗng nhưng KHÔNG nói đoạn
kéo về có đúng hay không. Hai số duy nhất trả lời được câu hỏi đó là recall@k và độ
chính xác trích xuất, và cả hai đều cần NHÃN do người có chuyên môn xác nhận.

Hạ tầng đã có sẵn từ trước: `retrieval.detail` trong báo cáo lưu mã đoạn theo TỪNG
trường, `extracted_fields` lưu giá trị theo từng trường. Nên phần đo chạy lại được
HOÀN TOÀN từ artefact của phiên đã chạy — không phải gọi lại mô hình, không phải OCR
lại. Một lượt đo mất vài giây thay vì vài chục phút.

Bố cục một hồ sơ có nhãn (`data/golden/<case_id>.json`):

    {
      "case_id": "G01",
      "session_id": "4166660c-...",        // phiên đã chạy, dùng làm nguồn artefact
      "note": "đã ẩn danh",
      "fields": {                          // GIÁ TRỊ ĐÚNG do người kiểm tra xác nhận
        "gia_tri_hop_dong": "120000000 VND",
        "thoi_han": "6 thang",
        "so_hop_dong": null                // null = trường này ĐÚNG LÀ không có
      },
      "relevant_chunks": {                 // đoạn quy định ĐÚNG cho từng trường (recall@k)
        "giai_quyet_tranh_chap": ["Bộ luật Dân sự::12::abc123"]
      },
      "citations": {                       // văn bản nguồn ĐÚNG cho kết luận từng trường
        "gia_tri_hop_dong": ["Bộ luật Dân sự"]
      }
    }

Chạy:  python -m app.eval            (hoặc GET /api/v1/admin/golden)
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.core import settings
from app.store.paths import DATA_DIR

GOLDEN_DIR = DATA_DIR / "golden"

# Tên các tệp nhãn không đọc được của lượt nạp gần nhất — `run_eval` đưa vào báo cáo.
_BAD_LABELS: list[str] = []


# ---------------------------------------------------------------------------
# So khớp giá trị — chuẩn hóa vừa đủ, không "sửa hộ" người nhập nhãn
# ---------------------------------------------------------------------------
_SPACE = re.compile(r"\s+")
_EDGE = re.compile(r"^[\s.,;:\-–—/()\"']+|[\s.,;:\-–—/()\"']+$")


def normalize(value: Any) -> str:
    """Chuẩn hóa một giá trị để so khớp: bỏ dấu · thường hóa · gộp khoảng trắng.

    Cố ý KHÔNG chuẩn hóa sâu hơn (không tự đổi '184.461' thành '184461', không tự
    dịch đơn vị). Chuẩn hóa càng mạnh thì độ chính xác đo được càng đẹp và càng xa sự
    thật; nhãn viết đúng dạng hệ trả ra là trách nhiệm của người dán nhãn."""
    if value is None:
        return ""
    s = str(value)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    return _EDGE.sub("", _SPACE.sub(" ", s))


def _numeric(s: str) -> float | None:
    """Số trong chuỗi, hiểu đúng dấu phân cách nghìn kiểu Việt Nam. None nếu không phải số.

    Ba lối viết cùng chỉ một số tiền: `184461` · `184.461` · `184,461`. Nhãn do người
    chép tay nên gặp cả ba; coi chúng là ba số khác nhau thì độ chính xác đo được tụt
    xuống vì một quy ước trình bày, không phải vì hệ trích sai."""
    raw = s.strip().replace(" ", "")
    if not re.fullmatch(r"[-+]?[\d.,]+", raw):
        return None
    dau = raw.lstrip("+-")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", dau):
        return float(raw[:len(raw) - len(dau)] + re.sub(r"[.,]", "", dau))
    # Còn lại: dấu XUẤT HIỆN SAU CÙNG là dấu thập phân, dấu kia là phân cách nghìn.
    raw = raw.replace(".", "") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def values_match(expected: Any, actual: Any) -> bool:
    """Giá trị hệ trích ra có khớp nhãn không.

    Ba mức, dừng ở mức đầu tiên khớp: (1) cùng rỗng; (2) cùng SỐ sau khi bỏ dấu phân
    cách nghìn; (3) chuỗi đã chuẩn hóa của bên này CHỨA bên kia — nhãn của trường điều
    khoản dài không thể chép nguyên cả đoạn, nên bao hàm là mức so hợp lý duy nhất."""
    e, a = normalize(expected), normalize(actual)
    if not e and not a:
        return True
    if not e or not a:
        return False
    ne, na = _numeric(e), _numeric(a)
    if ne is not None and na is not None:
        return abs(ne - na) < 1e-9
    return e in a or a in e


# ---------------------------------------------------------------------------
# Nạp nhãn + artefact của phiên
# ---------------------------------------------------------------------------
def load_cases() -> list[dict[str, Any]]:
    """Mọi hồ sơ có nhãn trong `data/golden/`. Thiếu thư mục -> danh sách rỗng.

    Tệp nhãn HỎNG bị bỏ qua chứ không chặn cả lượt đo, nhưng tên nó được ghi lại ở
    `_BAD_LABELS` để báo cáo nói ra — bỏ qua trong im lặng thì một nhãn sai cú pháp
    làm số đo tụt mà không ai biết vì sao."""
    _BAD_LABELS.clear()
    if not GOLDEN_DIR.exists():
        return []
    cases: list[dict[str, Any]] = []
    for p in sorted(GOLDEN_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — một nhãn hỏng không chặn cả lượt đo
            _BAD_LABELS.append(f"{p.name}: {exc}")
            continue
        data.setdefault("case_id", p.stem)
        cases.append(data)
    return cases


def _misplaced_artifacts() -> list[str]:
    """Thư mục artefact bị chép NHẦM vào `golden/` (có `documents.json` bên trong).

    Đây là nhầm lẫn tự nhiên nhất khi dùng module này: `golden/` chứa NHÃN — giá trị
    đúng do người có chuyên môn xác nhận — chứ không chứa đầu ra của hệ. Artefact vẫn
    nằm nguyên ở `temp/<phiên>/` và được nạp qua khóa `session_id` trong tệp nhãn.
    Chép artefact vào đây thì `glob("*.json")` không thấy gì, và báo cáo nói "chưa có
    hồ sơ nào có nhãn" — đúng chữ nhưng không chỉ ra chỗ sai."""
    if not GOLDEN_DIR.exists():
        return []
    return sorted(p.name for p in GOLDEN_DIR.iterdir()
                  if p.is_dir() and (p / "documents.json").exists())


def _session_artifacts(session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(documents.json, final_report.json) của một phiên; thiếu -> dict rỗng."""
    base = Path(settings.temp_dir) / session_id
    out: list[dict[str, Any]] = []
    for name in ("documents.json", "final_report.json"):
        try:
            out.append(json.loads((base / name).read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            out.append({})
    return out[0], out[1]


def _merged_fields(documents: dict[str, Any]) -> dict[str, Any]:
    """Giá trị đã trích của cả bộ hồ sơ: tài liệu ĐẦU có giá trị cho một trường thì thắng.

    Đúng bằng quy tắc gộp của `merge_contracts` (chỉ bù trường còn trống), nên số đo
    ở đây phản ánh cái người dùng thật sự nhìn thấy chứ không phải một phép gộp riêng."""
    out: dict[str, Any] = {}
    for d in documents.get("documents", []) or []:
        for k, f in ((d.get("contract") or {}).get("extracted_fields") or {}).items():
            v = f.get("value") if isinstance(f, dict) else f
            if k not in out or out[k] in (None, "", [], {}):
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# Ba phép đo
# ---------------------------------------------------------------------------
def extraction_accuracy(case: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Tỉ lệ trường trích ĐÚNG so với nhãn, kèm danh sách trường sai.

    Trường nhãn ghi `null` (đúng là không có) mà hệ vẫn điền giá trị thì tính SAI —
    đây chính là lỗi 'bắt bừa' mà mọi đợt nới điều kiện khớp đều làm tăng."""
    labels = case.get("fields") or {}
    wrong: list[dict[str, Any]] = []
    correct = 0
    for key, expected in labels.items():
        actual = fields.get(key)
        if values_match(expected, actual):
            correct += 1
        else:
            wrong.append({"field": key, "expected": expected, "actual": actual})
    total = len(labels)
    return {"total": total, "correct": correct,
            "accuracy": round(correct / total, 3) if total else None,
            "wrong": wrong}


def recall_at_k(case: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """recall@k THẬT: trong các đoạn quy định ĐÚNG của một trường, bao nhiêu phần được kéo về.

    Đây là số mà `coverage_at_k` không thay thế được: phủ truy hồi chỉ nói 'có kéo về
    đoạn nào đó', recall nói 'có kéo về ĐÚNG đoạn cần'."""
    want = case.get("relevant_chunks") or {}
    detail = ((report.get("metrics") or {}).get("retrieval") or {}).get("detail") or {}
    per_field: list[dict[str, Any]] = []
    hits = need = 0
    for key, expected in want.items():
        exp = {str(x) for x in (expected or [])}
        got = {str(x) for x in (detail.get(key) or [])}
        inter = exp & got
        hits += len(inter)
        need += len(exp)
        per_field.append({"field": key, "relevant": len(exp), "retrieved": len(inter),
                          "missed": sorted(exp - got)})
    return {"fields": len(want), "relevant": need, "retrieved": hits,
            "recall": round(hits / need, 3) if need else None,
            "detail": per_field}


def citation_accuracy(case: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Kết luận của một trường có dẫn ĐÚNG văn bản nguồn mà người kiểm tra chờ đợi không.

    So trên TÊN VĂN BẢN chứ không trên mã đoạn: mã đoạn đổi mỗi lần seed lại (nó gồm
    hàm băm nội dung), còn 'Thông tư số 02/2024/TT-BLĐTBXH' thì không."""
    want = case.get("citations") or {}
    by_check: dict[str, set[str]] = {}
    for d in report.get("documents", []) or []:
        for ck in d.get("checks", []) or []:
            cid = str(ck.get("check_id") or "")
            by_check.setdefault(cid, set()).update(
                normalize(c.get("source_doc")) for c in (ck.get("citations") or []))
    ok = 0
    misses: list[dict[str, Any]] = []
    for key, expected in want.items():
        got = by_check.get(key, set())
        if any(normalize(e) in got for e in (expected or [])):
            ok += 1
        else:
            misses.append({"check_id": key, "expected": expected, "actual": sorted(got)})
    total = len(want)
    return {"total": total, "correct": ok,
            "accuracy": round(ok / total, 3) if total else None, "misses": misses}


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Ba phép đo cho MỘT hồ sơ có nhãn. Thiếu artefact phiên -> báo rõ, không đo bừa."""
    sid = str(case.get("session_id") or "")
    documents, report = _session_artifacts(sid)
    if not documents:
        return {"case_id": case.get("case_id"), "session_id": sid, "ok": False,
                "error": f"Không thấy artefact của phiên {sid or '(chưa khai)'} trong temp/."}
    return {
        "case_id": case.get("case_id"),
        "session_id": sid,
        "ok": True,
        # `verified` = đã có người có chuyên môn ĐỐI CHIẾU từng giá trị với bản gốc.
        # Nhãn dựng sẵn từ chính đầu ra của hệ thì để `false`: nó vẫn đo được (bắt
        # được hồi quy — lần chạy sau lệch đi là biết ngay), nhưng con số tuyệt đối
        # KHÔNG nói lên độ chính xác, vì chuẩn và bài thi là cùng một thứ.
        "verified": bool(case.get("verified")),
        "extraction": extraction_accuracy(case, _merged_fields(documents)),
        "retrieval": recall_at_k(case, report),
        "citation": citation_accuracy(case, report),
    }


def run_eval() -> dict[str, Any]:
    """Chạy toàn bộ golden corpus và gộp số. Không có hồ sơ nhãn -> nói thẳng là chưa có.

    Gộp theo TỔNG SỐ TRƯỜNG, không phải trung bình của các tỉ lệ theo hồ sơ: hồ sơ dán
    nhãn 3 trường và hồ sơ dán nhãn 40 trường không được có cùng trọng số."""
    cases = load_cases()
    results = [evaluate_case(c) for c in cases]
    good = [r for r in results if r.get("ok")]

    def _sum(group: str, num: str, den: str) -> tuple[int, int]:
        return (sum(r[group][num] for r in good), sum(r[group][den] for r in good))

    ex_ok, ex_all = _sum("extraction", "correct", "total")
    rc_ok, rc_all = _sum("retrieval", "retrieved", "relevant")
    ct_ok, ct_all = _sum("citation", "correct", "total")
    return {
        "cases": results,
        "summary": {
            "cases_total": len(results),
            "cases_measured": len(good),
            "cases_unverified": sum(1 for r in good if not r["verified"]),
            "extraction_accuracy": round(ex_ok / ex_all, 3) if ex_all else None,
            "extraction_fields": ex_all,
            "recall_at_k": round(rc_ok / rc_all, 3) if rc_all else None,
            "relevant_chunks": rc_all,
            "citation_accuracy": round(ct_ok / ct_all, 3) if ct_all else None,
            "citation_checks": ct_all,
        },
        "golden_dir": str(GOLDEN_DIR),
        "note": _note(cases),
        "bad_labels": list(_BAD_LABELS),
    }


def _note(cases: list[dict[str, Any]]) -> str:
    """Câu giải thích khi lượt đo không ra số — phải chỉ ĐÚNG chỗ sai, không nói chung.

    Ba tình huống khác hẳn nhau: chưa dán nhãn gì · chép nhầm artefact vào đây · nhãn
    sai cú pháp. Gộp cả ba vào một câu 'chưa có hồ sơ nào có nhãn' thì người đọc đi
    dán thêm nhãn trong khi bệnh nằm ở chỗ khác."""
    hong = (f" Ngoài ra {len(_BAD_LABELS)} tệp nhãn không đọc được: "
            + " · ".join(_BAD_LABELS)) if _BAD_LABELS else ""
    if cases:
        if chua := [str(c.get("case_id")) for c in cases if not c.get("verified")]:
            return (f"{len(chua)} hồ sơ ({' · '.join(chua)}) có nhãn CHƯA ĐƯỢC NGƯỜI "
                    "XÁC NHẬN — nhãn dựng sẵn từ chính đầu ra của hệ. Số đo dùng được "
                    "để bắt hồi quy (lần chạy sau lệch đi là biết), nhưng KHÔNG dùng "
                    "được làm độ chính xác trong báo cáo: chuẩn và bài thi đang là một. "
                    "Đối chiếu từng giá trị với bản gốc rồi đặt \"verified\": true."
                    + hong)
        return hong.strip()
    if nham := _misplaced_artifacts():
        return (f"Thư mục nhãn đang chứa {len(nham)} THƯ MỤC ARTEFACT ({' · '.join(nham)}) "
                "chứ không phải tệp nhãn. Artefact đã nằm sẵn ở backend/temp/<phiên> và "
                "được nạp qua khóa session_id, nên chép vào đây thì không đo được gì. "
                "Xóa các thư mục đó, rồi đặt tệp nhãn .json (giá trị ĐÚNG do người kiểm "
                "tra xác nhận — xem docstring app/eval.py) vào thư mục này." + hong)
    return ("Chưa có hồ sơ nào có nhãn. Đặt tệp nhãn .json vào thư mục trên "
            "(xem docstring app/eval.py) rồi chạy lại." + hong)


def print_report(res: dict[str, Any]) -> None:
    """In gọn kết quả một lượt đo. Dùng chung cho `python -m app.eval` và `npm run check`
    nên hai lối chạy không thể in ra hai bản khác nhau của cùng một phép đo."""
    s = res["summary"]
    print(f"[eval] {s['cases_measured']}/{s['cases_total']} hồ sơ đo được. Thư mục nhãn: {res['golden_dir']}")
    if res.get("note"):
        print("[eval] " + res["note"])
    if not s["cases_total"]:
        return
    chua = "  (CHƯA XÁC NHẬN)" if s.get("cases_unverified") else ""
    print(f"[eval] Độ chính xác trích xuất : {s['extraction_accuracy']} trên {s['extraction_fields']} trường{chua}")
    print(f"[eval] recall@k               : {s['recall_at_k']} trên {s['relevant_chunks']} đoạn quy định đúng")
    print(f"[eval] Độ chính xác trích dẫn : {s['citation_accuracy']} trên {s['citation_checks']} kết luận")
    for r in res["cases"]:
        if not r.get("ok"):
            print(f"  - {r['case_id']}: {r['error']}")
            continue
        for w in r["extraction"]["wrong"]:
            print(f"  - {r['case_id']} SAI {w['field']}: nhãn={w['expected']!r} hệ={w['actual']!r}")


if __name__ == "__main__":
    print_report(run_eval())


__all__ = [
    "GOLDEN_DIR", "citation_accuracy", "evaluate_case", "extraction_accuracy",
    "load_cases", "normalize", "print_report", "recall_at_k", "run_eval", "values_match",
]
