"""HẠ TẦNG (store.audit) — NHẬT KÝ kiểm tra (data/audit.jsonl).

Mỗi lần kiểm tra ghi 1 dòng JSON. Từ đó dựng: kho hồ sơ tra cứu, thống kê PASS/FAIL
theo bộ trường (loại hồ sơ) và chỉ số kỹ thuật.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from .app_settings import stats_since
from .paths import AUDIT_FILE, DATA_DIR, VERDICTS, file_lock


def record_run(
    session_id: str,
    field_set_id: str,
    field_set_name: str,
    documents: list[dict[str, Any]],
    signed_date: str = "",
    metrics: dict[str, Any] | None = None,
    total_bytes: int = 0,
    total_pages: int = 0,
    ocr_seconds: float = 0.0,
    corpus_fingerprint: str = "",
    source_files: list[str] | None = None,
    accuracy: dict[str, Any] | None = None,
) -> None:
    """Ghi 1 bản ghi kiểm tra (gồm kết luận từng document).

    `metrics` lưu bản GỌN của số đo lượt này (thời gian, phủ truy hồi, độ chính xác
    trích dẫn, cache, retry) — không lưu `retrieval.detail` vì nó phình theo số đoạn.

    `total_bytes` + `total_pages` + `ocr_seconds` thuộc bước ĐỌC HỒ SƠ (chạy ở lượt
    tải lên) nên phải truyền vào riêng."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "session_id": session_id,
            "field_set_id": field_set_id or "",
            "field_set_name": field_set_name or "",
            "signed_date": signed_date or "",
            "source_files": list(source_files or []),
            "num_documents": len(documents),
            "total_bytes": int(total_bytes or 0),
            "total_pages": int(total_pages or 0),
            "ocr_seconds": round(float(ocr_seconds or 0.0), 1),
            # Vân tay kho quy định lúc chạy: bản ghi cũ có vân tay khác vân tay hiện tại là
            # bản ghi phải kiểm lại trước khi đem dùng làm bằng chứng.
            "corpus_fingerprint": corpus_fingerprint or "",
            "documents": [
                {
                    "doc_id": d.get("doc_id"),
                    "source_file": d.get("source_file", ""),
                    "overall_verdict": d.get("overall_verdict", "NEEDS_SUPPLEMENT"),
                    "num_fail": sum(1 for c in d.get("checks", []) if c.get("verdict") == "FAIL"),
                }
                for d in documents
            ],
        }
        if metrics:
            rec["metrics"] = _slim_metrics(metrics)
        if accuracy:
            # CER/WER/độ chính xác trường… của bộ hồ sơ này (xem compliance.accuracy).
            rec["accuracy"] = accuracy
        # KHÓA quanh phần ghi thêm: hai tiến trình cùng ghi một tệp JSONL không khóa
        # thì hai dòng chèn lẫn vào nhau và mất cả hai bản ghi.
        with file_lock(AUDIT_FILE), open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - không để lỗi ghi log làm hỏng luồng kiểm tra
        pass


def _slim_metrics(m: dict[str, Any]) -> dict[str, Any]:
    """Bản gọn để ghi nhật ký: bỏ `history` (tính lại được) và `retrieval.detail`."""
    lat = m.get("latency", {}) or {}
    ret = {k: v for k, v in (m.get("retrieval") or {}).items() if k != "detail"}
    return {
        "seconds": lat.get("total_seconds"),
        "stages": lat.get("stages", {}),
        "retrieval": ret,
        "citation": m.get("citation", {}),
        "payload": m.get("payload", {}),
        "cache_hit_ratio": (m.get("cache") or {}).get("hit_ratio"),
        "resilience": m.get("resilience", {}),
        "peak_rss_mb": m.get("peak_rss_mb"),
    }


def latency_percentiles(limit: int = 200) -> dict[str, Any]:
    """p50/p95 thời gian kiểm tra trên `limit` lượt gần nhất đã ghi nhật ký.

    Một lượt chỉ cho một con số nên phân vị BẮT BUỘC phải tính trên lịch sử. Trả kèm
    `samples` để người đọc biết con số dựa trên bao nhiêu lượt — p95 của 3 lượt không
    có ý nghĩa thống kê, và nếu không nói ra thì nó trông y hệt p95 của 200 lượt."""
    from app.metrics import percentile  # noqa: PLC0415 — tránh vòng import

    totals: list[float] = []
    by_stage: dict[str, list[float]] = {}
    for rec in _read_all()[-limit:]:
        m = rec.get("metrics") or {}
        if isinstance(m.get("seconds"), (int, float)):
            totals.append(float(m["seconds"]))
        for name, sec in (m.get("stages") or {}).items():
            if isinstance(sec, (int, float)):
                by_stage.setdefault(name, []).append(float(sec))
    return {
        "samples": len(totals),
        "total_p50": percentile(totals, 0.50),
        "total_p95": percentile(totals, 0.95),
        "stages_p50": {k: percentile(v, 0.50) for k, v in by_stage.items()},
        "stages_p95": {k: percentile(v, 0.95) for k, v in by_stage.items()},
    }


# Ảnh chụp nhật ký đã phân tích, khóa theo (mtime_ns, kích thước) của chính tệp.
# Vì sao cần: MỘT lần mở trang Chỉ số kỹ thuật gọi `technical_metrics` ->
# `latency_percentiles` -> `quality_metrics`, ba hàm đều duyệt toàn bộ nhật ký, nên
# `audit.jsonl` bị đọc và json.loads ba lượt cho cùng một nội dung.
# Khóa theo mtime + kích thước chứ không theo thời gian sống: tệp chỉ được GHI THÊM
# bởi chính tiến trình này, nên hễ nội dung đổi là một trong hai số đổi theo và ảnh
# chụp tự hết hiệu lực ngay lượt đọc kế tiếp — không có cửa sổ trả dữ liệu cũ.
_CACHE: tuple[tuple[str, int, int], list[dict[str, Any]]] | None = None


def _read_all() -> list[dict[str, Any]]:
    """Mọi bản ghi theo thứ tự ghi (cũ trước). Dòng hỏng -> bỏ qua, không nổ."""
    global _CACHE
    try:
        st = AUDIT_FILE.stat()
    except OSError:
        return []
    # ĐƯỜNG DẪN nằm trong khóa: bộ test trỏ `AUDIT_FILE` sang tệp tạm khác nhau cho
    # mỗi ca, và hai tệp khác nhau hoàn toàn có thể trùng cả kích thước lẫn mốc thời
    # gian khi hệ tệp chỉ ghi mtime ở độ phân giải thô.
    dau = (str(AUDIT_FILE), st.st_mtime_ns, st.st_size)
    if _CACHE is not None and _CACHE[0] == dau:
        return _CACHE[1]
    out: list[dict[str, Any]] = []
    with open(AUDIT_FILE, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    _CACHE = (dau, out)
    return out


def read_audit(limit: int = 200) -> list[dict[str, Any]]:
    """Danh sách lần kiểm tra gần nhất (mới nhất trước)."""
    return _read_all()[::-1][:limit]


def search_audit(
    q: str = "", field_set: str = "", verdict: str = "", limit: int = 200,
) -> list[dict[str, Any]]:
    """KHO HỒ SƠ: tìm toàn văn (bỏ dấu) + lọc theo bộ trường/kết quả trên nhật ký kiểm
    tra. q khớp trên: tên file, tên bộ trường, session_id."""
    from app.domain.documents.ocr import (
        fold_diacritics,  # noqa: PLC0415 — tránh vòng import
    )

    qf = fold_diacritics(q or "").lower().strip()
    out: list[dict[str, Any]] = []
    for rec in reversed(_read_all()):
        docs = rec.get("documents", [])
        if field_set and field_set not in (rec.get("field_set_id"), rec.get("field_set_name")):
            continue
        if verdict and not any(d.get("overall_verdict") == verdict for d in docs):
            continue
        if qf:
            hay = fold_diacritics(" ".join(
                [rec.get(k, "") for k in ("session_id", "field_set_name")]
                + list(rec.get("source_files") or [])
                + [d.get("source_file", "") for d in docs]
            )).lower()
            if qf not in hay:
                continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


_ACC_KEYS = ("cer", "wer", "ocr_accuracy", "field_accuracy", "table_accuracy",
             "number_accuracy", "date_accuracy")


def _mean_accuracy(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Trung bình từng chỉ số trên các bộ hồ sơ CÓ chỉ số đó (`None` bị bỏ qua)."""
    out: dict[str, Any] = {"runs": len(items)}
    for k in _ACC_KEYS:
        vals = [float(a[k]) for a in items if isinstance(a.get(k), (int, float))]
        out[k] = round(sum(vals) / len(vals), 4) if vals else None
    return out


def aggregate_stats(recent: int = 50) -> dict[str, Any]:
    """Tổng hợp PASS/FAIL/NEEDS_SUPPLEMENT theo BỘ TRƯỜNG (tính trên từng document),
    kèm CHỈ SỐ CHẤT LƯỢNG ĐỌC (CER, WER, độ chính xác…) — trung bình theo bộ trường và
    từng bộ hồ sơ gần nhất (`runs`, mới nhất trước)."""
    by_field_set: dict[str, dict[str, Any]] = {}
    totals = dict.fromkeys(VERDICTS, 0)
    total_docs = total_runs = 0
    runs: list[dict[str, Any]] = []
    acc_by_set: dict[str, list[dict[str, Any]]] = {}
    since = stats_since()      # "Xóa thống kê" ở trang Cài đặt: chỉ tính lượt SAU mốc này
    for rec in _read_all():
        if since and str(rec.get("ts", "")) < since:
            continue
        total_runs += 1
        name = rec.get("field_set_name") or rec.get("field_set_id") or "(không rõ)"
        if isinstance(acc := rec.get("accuracy"), dict):
            acc_by_set.setdefault(name, []).append(acc)
            runs.append({"ts": rec.get("ts", ""), "session_id": rec.get("session_id", ""),
                         "field_set_name": name,
                         "source_files": rec.get("source_files") or [],
                         "accuracy": acc})
        m = by_field_set.setdefault(name, dict.fromkeys(VERDICTS, 0) | {"total": 0})
        for d in rec.get("documents", []):
            v = d.get("overall_verdict", "NEEDS_SUPPLEMENT")
            if v not in VERDICTS:
                v = "NEEDS_SUPPLEMENT"
            m[v] += 1
            m["total"] += 1
            totals[v] += 1
            total_docs += 1
    for name, m in by_field_set.items():
        m["accuracy"] = _mean_accuracy(acc_by_set.get(name, []))
    runs.sort(key=lambda r: str(r["ts"]), reverse=True)
    return {
        "by_field_set": by_field_set,
        "totals": totals | {"total": total_docs},
        "total_runs": total_runs,
        "accuracy": _mean_accuracy([a for v in acc_by_set.values() for a in v]),
        "runs": runs[:max(0, recent)],
    }


def _derive_rates(d: dict[str, Any]) -> None:
    """Điền `mb` + đơn giá thời gian cho một dòng đã cộng xong (sửa tại chỗ).

    Đơn giá tính trên TỔNG thời gian (OCR + kiểm tra) vì đó là thứ người vận hành
    thực sự chờ. Mẫu số bằng 0 -> `None` chứ không phải 0: "chưa đo được" và "xử lý
    tức thì" là hai chuyện khác nhau, hiển thị 0 s/trang sẽ nói dối cái thứ hai."""
    d["check_seconds"] = round(d["check_seconds"], 1)
    d["ocr_seconds"] = round(d["ocr_seconds"], 1)
    d["mb"] = round(d["bytes"] / 1048576, 2)
    span = d["ocr_seconds"] + d["check_seconds"]
    d["seconds_per_page"] = round(span / d["pages"], 1) if d["pages"] else None
    d["seconds_per_file"] = round(span / d["files"], 1) if d["files"] else None


def technical_metrics(days: int = 30) -> dict[str, Any]:
    """CHỈ SỐ KỸ THUẬT gom THEO PHIÊN LÀM VIỆC — nguồn của trang quản trị cùng tên.

    Mỗi phiên (`session_id`) trả: số lượt kiểm tra, số file, số trang, dung lượng,
    thời gian OCR, thời gian kiểm tra, và đơn giá thời gian mỗi trang / mỗi file.
    Gom theo PHIÊN chứ không theo ngày: một ngày có thể trộn nhiều hồ sơ kích thước
    rất khác nhau nên tổng theo ngày không so sánh được với nhau, trong khi phiên là
    đơn vị công việc thật — người vận hành hỏi "hồ sơ này mất bao lâu", không hỏi
    "hôm qua tổng cộng bao nhiêu giây".

    Kiểm tra lại cùng một phiên sẽ cộng dồn vào chính phiên đó (`runs` > 1), vì cả
    hai lượt đều là chi phí bỏ ra cho một hồ sơ.

    Bản ghi CŨ thiếu `total_bytes`/`total_pages`/`ocr_seconds`; chúng được tính là 0
    chứ KHÔNG bị loại, nếu không những phiên đó biến mất và trông như hệ thống ngừng
    chạy. Phiên thiếu số trang thì đơn giá là `None` (hiện "—") thay vì một con số bịa."""
    cutoff = (datetime.now().date() - timedelta(days=max(1, days) - 1)).isoformat()
    by_session: dict[str, dict[str, Any]] = {}
    since = stats_since()
    for rec in _read_all():
        ts = str(rec.get("ts", ""))
        if since and ts < since:
            continue
        sid = str(rec.get("session_id") or "")
        if not ts or ts[:10] < cutoff or not sid:
            continue
        s = by_session.setdefault(sid, {
            "session_id": sid, "ts": ts, "field_set_name": "",
            "runs": 0, "files": 0, "pages": 0, "bytes": 0,
            "ocr_seconds": 0.0, "check_seconds": 0.0,
        })
        s["ts"] = max(s["ts"], ts)            # mốc thời gian = LƯỢT MỚI NHẤT của phiên
        s["field_set_name"] = rec.get("field_set_name") or s["field_set_name"]
        s["runs"] += 1
        s["files"] += int(rec.get("num_documents") or 0)
        s["pages"] += int(rec.get("total_pages") or 0)
        s["bytes"] += int(rec.get("total_bytes") or 0)
        s["ocr_seconds"] += float(rec.get("ocr_seconds") or 0.0)
        sec = (rec.get("metrics") or {}).get("seconds")
        if isinstance(sec, (int, float)):
            s["check_seconds"] += float(sec)
    sessions = sorted(by_session.values(), key=lambda r: r["ts"], reverse=True)
    total = {
        k: sum(s[k] for s in sessions)
        for k in ("runs", "files", "pages", "bytes", "ocr_seconds", "check_seconds")
    }
    for s in sessions:
        _derive_rates(s)
    _derive_rates(total)
    # Vân tay kho quy định tính MỘT lần ở đây rồi truyền xuống: `quality_metrics` cần nó để
    # đếm bản ghi lạc hậu, mà tính lại cho từng bản ghi là băm lại cả bộ văn bản quy định.
    try:
        from app.domain.regulations import corpus  # noqa: PLC0415 — tránh vòng import
        fingerprint = corpus.corpus_fingerprint()
    except Exception:  # noqa: BLE001 — kho quy định hỏng không được làm mất trang chỉ số
        fingerprint = ""
    return {"sessions": sessions, "total": total, "window_days": days,
            "latency": latency_percentiles(),
            "quality": quality_metrics(days=days, current_fingerprint=fingerprint)}


def _avg(xs: list[float]) -> float | None:
    """Trung bình, hoặc None khi không có mẫu — 0 và 'chưa đo' là hai chuyện khác nhau."""
    return round(sum(xs) / len(xs), 3) if xs else None


def quality_metrics(days: int = 30, current_fingerprint: str = "") -> dict[str, Any]:
    """CHỈ SỐ CHẤT LƯỢNG gộp trên nhật ký: phủ truy hồi · độ chính xác trích dẫn · tải LLM.

    Vì sao phải có chỗ hiển thị: hai số này vẫn được ghi vào `audit.jsonl` sau mỗi lượt
    nhưng từ lúc tách trang chỉ số thì không còn nơi nào đọc chúng. Độ chính xác trích
    dẫn là phép đo DUY NHẤT bắt được model BỊA nguồn mà không cần bộ nhãn — mất chỗ
    hiển thị là mất luôn khả năng phát hiện, và lỗi bịa nguồn thì im lặng tuyệt đối.

    `uncovered_fields` gom theo TRƯỜNG chứ không theo lượt: một trường trượt truy hồi ở
    nhiều lượt là một lỗ hổng của kho quy định, không phải một sự cố ngẫu nhiên.

    `stale_runs` đếm bản ghi có vân tay kho quy định KHÁC vân tay hiện tại — những kết luận
    đã sinh ra trên một bộ căn cứ không còn tồn tại."""
    cutoff = (datetime.now().date() - timedelta(days=max(1, days) - 1)).isoformat()
    cov: list[float] = []
    full_cov: list[float] = []
    prec: list[float] = []
    grounded: list[float] = []
    tokens: list[float] = []
    ctx_used: list[float] = []
    ctx_cfg: list[float] = []
    fields_sent: list[float] = []
    hallucinated = citations = decided = runs = stale = no_fp = 0
    uncovered: dict[str, int] = {}

    for rec in _read_all():
        if str(rec.get("ts", ""))[:10] < cutoff:
            continue
        m = rec.get("metrics") or {}
        if not m:
            continue
        runs += 1
        fp = str(rec.get("corpus_fingerprint") or "")
        if not fp:
            no_fp += 1
        elif current_fingerprint and fp != current_fingerprint:
            stale += 1

        ret = m.get("retrieval") or {}
        if isinstance(ret.get("coverage_at_k"), (int, float)):
            cov.append(float(ret["coverage_at_k"]))
        if isinstance(ret.get("full_coverage_at_k"), (int, float)):
            full_cov.append(float(ret["full_coverage_at_k"]))
        for k in (ret.get("uncovered_fields") or []):
            uncovered[str(k)] = uncovered.get(str(k), 0) + 1

        cit = m.get("citation") or {}
        if isinstance(cit.get("precision"), (int, float)):
            prec.append(float(cit["precision"]))
        if isinstance(cit.get("grounded_ratio"), (int, float)):
            grounded.append(float(cit["grounded_ratio"]))
        hallucinated += int(cit.get("hallucinated") or 0)
        citations += int(cit.get("citations") or 0)
        decided += int(cit.get("decided_checks") or 0)

        pl = m.get("payload") or {}
        for src, dst in ((pl.get("tokens_est"), tokens), (pl.get("num_ctx_used"), ctx_used),
                         (pl.get("num_ctx_config"), ctx_cfg), (pl.get("fields"), fields_sent)):
            if isinstance(src, (int, float)) and src:
                dst.append(float(src))

    return {
        "runs": runs,
        "window_days": days,
        "retrieval": {
            "coverage_avg": _avg(cov),
            "full_coverage_avg": _avg(full_cov),
            "samples": len(cov),
            "uncovered_fields": sorted(
                ({"field": k, "runs": v} for k, v in uncovered.items()),
                key=lambda d: (-d["runs"], d["field"]))[:20],
        },
        "citation": {
            "precision_avg": _avg(prec),
            "grounded_ratio_avg": _avg(grounded),
            "citations": citations,
            "hallucinated": hallucinated,
            "decided_checks": decided,
        },
        "payload": {
            "tokens_avg": _avg(tokens),
            "fields_avg": _avg(fields_sent),
            "num_ctx_used_avg": _avg(ctx_used),
            "num_ctx_config_avg": _avg(ctx_cfg),
            # Cửa sổ cấp phát mà không dùng tới — chính là phần RAM trả giá vô ích.
            "num_ctx_headroom_avg": (
                round(_avg(ctx_cfg) - _avg(ctx_used), 1)
                if ctx_cfg and ctx_used else None),
        },
        "corpus": {
            "current_fingerprint": current_fingerprint,
            "stale_runs": stale,
            "runs_without_fingerprint": no_fp,
        },
    }


def clear_audit() -> dict[str, Any]:
    """XÓA nhật ký kiểm tra — nguồn DUY NHẤT của trang Thống kê (`aggregate_stats`) và
    trang Lịch sử (`read_audit`/`search_audit`). Xóa file này là cả hai cùng về 0.

    Trả về số bản ghi đã xóa + xác nhận thống kê đã rỗng, để `npm run clear` và nút
    trên trang Thống kê nói được ĐÚNG hệ quả thay vì chỉ "đã xóa"."""
    n = len(_read_all())
    ok = True
    try:
        if AUDIT_FILE.exists():
            os.remove(AUDIT_FILE)
    except OSError:
        # Windows: file đang bị tiến trình khác giữ -> không xóa được thì CẮT RỖNG.
        # Cắt rỗng không được nữa thì `ok=False`, và số bản ghi báo về phải là 0 —
        # báo "đã xóa n bản ghi" trong khi thống kê không đổi là nói dối người dùng.
        try:
            AUDIT_FILE.write_text("", encoding="utf-8")
        except OSError:
            ok = False
    remaining = len(_read_all())
    return {
        "deleted_records": n if ok else 0,
        "remaining_records": remaining,
        "stats_reset": remaining == 0,
    }
