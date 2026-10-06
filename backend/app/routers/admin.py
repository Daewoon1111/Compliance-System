"""TẦNG API (admin) — TRANG QUẢN TRỊ (cần mã quản trị): sửa cấu hình MẶC ĐỊNH của hệ thống.

Cho sửa: bộ trường công việc (jobs), danh mục thị trường (markets.json) và văn bản
luật (rules) — sửa luật tự re-seed ChromaDB. Kèm endpoint KIỂM TRA DATABASE (kho
quy định ChromaDB + dữ liệu phiên) để quản trị viên biết hệ đang ở trạng thái nào.

Cấu hình DỊCH VỤ (extraction/validation/checks) vẫn chỉ sửa trực tiếp trên file.
Cấu hình RIÊNG của người dùng nằm ở router `config.py` (không cần mã quản trị).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from app.core import settings
from app.store import applied_config_ids, technical_metrics, user_config_list


def _require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> None:
    """Auth cho /admin/*: mọi request phải kèm `Authorization: Bearer <admin_token>`
    (hoặc X-Admin-Token cũ). Mã luôn tồn tại (`core.ensure_admin_token`); trường hợp
    vẫn trống thì TỪ CHỐI — không có chế độ tắt xác thực.
    So sánh bằng compare_digest chống timing attack — trên BYTES vì compare_digest ném
    TypeError với chuỗi ngoài ASCII (mã có dấu/emoji sẽ thành 500 thay vì 401)."""
    if not (settings.admin_token or "").strip():
        raise HTTPException(status_code=401, detail="Máy chủ chưa có mã quản trị.")
    supplied = x_admin_token or ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not secrets.compare_digest(supplied.encode(), settings.admin_token.encode()):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai mã quản trị (Bearer token)")


router = APIRouter(
    prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(_require_admin)],
)
# Dependency dùng lại cho endpoint QUẢN TRỊ nằm ngoài prefix /admin (vd xóa nhật ký).
require_admin = _require_admin

APP_DIR = Path(__file__).resolve().parent.parent
# Bộ trường chia 3 TẦNG: khu vực -> quốc gia -> công việc (xem store.resolve_job_prompt).
_JOBS = APP_DIR / "prompts" / "jobs"
_ADMIN_DIRS = {
    "regions": _JOBS / "regions",
    "countries": _JOBS / "regions" / "countries",
    "works": _JOBS / "regions" / "countries" / "works",
    "markets": _JOBS,
    "rules": APP_DIR / "rules",
}
# Nhóm chỉ cho sửa ĐÚNG một file (markets.json nằm chung thư mục jobs với các tầng
# bộ trường) -> khai riêng để không mở nhầm quyền ghi cả thư mục.
_ADMIN_ONLY_FILES = {"markets": {"markets.json"}}


def _admin_display(grp: str, p) -> str:
    """Tên hiển thị TIẾNG VIỆT CÓ DẤU cho từng file cấu hình."""
    try:
        if grp in ("regions", "countries", "works"):
            return json.loads(p.read_text(encoding="utf-8")).get("display_name", p.stem)
        if grp == "markets":
            return "Danh mục khu vực / thị trường / loại hình lao động"
        if grp == "rules":
            for line in p.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("#"):
                    return (s.lstrip("#").strip().split("—")[0].strip()) or p.name
    except Exception:  # noqa: BLE001
        pass
    return p.name


def _admin_list() -> list[dict]:
    out: list[dict] = []
    for grp, base in _ADMIN_DIRS.items():
        if not base.exists():
            continue
        only = _ADMIN_ONLY_FILES.get(grp)
        for p in sorted(base.iterdir()):
            if not p.is_file() or p.suffix not in (".json", ".md"):
                continue
            if only is not None and p.name not in only:
                continue
            out.append({
                "group": grp,
                "name": p.name,
                "rel": f"{grp}/{p.name}",
                "type": p.suffix.lstrip("."),
                "display": _admin_display(grp, p),
            })
    return out


def _admin_resolve(rel: str):
    """Chỉ cho phép sửa file trong các thư mục cấu hình (chống path traversal)."""
    if "/" not in rel:
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ")
    grp, name = rel.split("/", 1)
    base = _ADMIN_DIRS.get(grp)
    if base is None or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ")
    only = _ADMIN_ONLY_FILES.get(grp)
    if only is not None and name not in only:
        raise HTTPException(status_code=400, detail="Không được phép sửa file này")
    p = (base / name).resolve()
    if base.resolve() not in p.parents or p.suffix not in (".json", ".md"):
        raise HTTPException(status_code=400, detail="Không được phép sửa file này")
    return p


@router.get("/files")
def admin_files():
    """Cây file cấu hình + văn bản luật sửa được trên trang quản trị."""
    return {"files": _admin_list()}


@router.get("/metrics")
def admin_metrics(days: int = 30):
    """CHỈ SỐ KỸ THUẬT theo PHIÊN LÀM VIỆC — nguồn của trang quản trị cùng tên.

    Nằm dưới `/admin` chứ không phải trong báo cáo của một phiên: đây là số liệu VẬN
    HÀNH của cả hệ (thông lượng, dung lượng đã xử lý, thời gian bỏ ra), không phải
    kết luận pháp lý của một bộ hồ sơ. `days` chỉ là cửa sổ LỌC phiên theo thời gian,
    không còn là đơn vị gom nhóm."""
    return technical_metrics(days=max(1, min(365, days)))


@router.get("/corpus")
def admin_corpus():
    """QUẢN TRỊ KHO LUẬT: từng văn bản kèm nguồn · phiên bản · hiệu lực · hàm băm · người duyệt.

    Đối chiếu đăng bạ `rules/corpus.json` với file thật trên đĩa. Trả kèm VÂN TAY của
    cả kho — chính con số nằm trong chữ ký yêu cầu kiểm tra, nên đổi kho là mọi báo cáo
    lưu sẵn tự động phải chạy lại."""
    from app.domain.regulations import corpus  # noqa: PLC0415 — nạp trễ, tránh vòng import

    corpus.sync_registry()   # file mới thêm tay vào rules/ vẫn hiện ra, ở trạng thái chưa khai
    return corpus.audit_corpus()


@router.post("/corpus/approve")
def admin_corpus_approve(body: dict = Body(...)):
    """PHÊ DUYỆT một văn bản luật: chốt hàm băm hiện tại + ghi người duyệt và thời điểm.

    Người duyệt phải khai tên — phê duyệt vô danh thì cột 'người phê duyệt' chỉ là
    trang trí, không truy được trách nhiệm."""
    from app.domain.regulations import corpus  # noqa: PLC0415

    file = str(body.get("file") or "").strip()
    who = str(body.get("approved_by") or "").strip()
    if not file or not who:
        raise HTTPException(status_code=400, detail="Thiếu tên file hoặc người phê duyệt")
    try:
        return corpus.approve(file, who, datetime.now().astimezone().isoformat(timespec="seconds"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Không thấy file {file}") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"{file} chưa có trong đăng bạ") from exc


@router.get("/golden")
def admin_golden():
    """ĐO THẬT trên golden corpus: recall@k · độ chính xác trích xuất · độ chính xác trích dẫn.

    Chạy lại hoàn toàn từ artefact phiên đã lưu nên không gọi mô hình, không OCR lại —
    bấm được ngay trên trang quản trị."""
    from app.eval import run_eval  # noqa: PLC0415 — nạp trễ, chỉ dùng ở đây

    return run_eval()


@router.get("/db")
def admin_db():
    """KIỂM TRA DATABASE: kho quy định (ChromaDB) + dữ liệu phiên + cấu hình người dùng.

    Mỗi mục trả `ok` riêng để một thành phần hỏng không làm mất thông tin của phần còn lại."""
    out: dict = {}

    # 1) Kho quy định ChromaDB — số đoạn luật đã nạp, theo từng văn bản nguồn.
    try:
        from app.domain.regulations import get_collection  # noqa: PLC0415

        col = get_collection()
        total = int(col.count())
        by_doc: dict[str, int] = {}
        if total:
            got = col.get(include=["metadatas"], limit=min(total, 5000))
            for m in (got.get("metadatas") or []):
                name = str((m or {}).get("source_doc") or "?")
                by_doc[name] = by_doc.get(name, 0) + 1
        out["chroma"] = {
            "ok": True, "chunks": total,
            "by_source": [{"source_doc": k, "chunks": v} for k, v in sorted(by_doc.items())],
            "warning": "" if total else "Kho quy định TRỐNG — chạy `npm run seed` hoặc lưu lại một văn bản luật để nạp.",
        }
    except Exception as exc:  # noqa: BLE001
        out["chroma"] = {"ok": False, "chunks": 0, "by_source": [], "error": str(exc)}

    # 2) Dữ liệu phiên (temp) + nhật ký kiểm tra.
    try:
        from app.store import AUDIT_FILE  # noqa: PLC0415

        temp_dir = Path(settings.temp_dir)
        sessions = [p for p in temp_dir.iterdir() if p.is_dir()] if temp_dir.exists() else []
        size = sum(f.stat().st_size for p in sessions for f in p.rglob("*") if f.is_file())
        audit_lines = 0
        if AUDIT_FILE.exists():
            audit_lines = sum(1 for _ in AUDIT_FILE.open(encoding="utf-8"))
        out["sessions"] = {"ok": True, "count": len(sessions),
                           "size_mb": round(size / 1_048_576, 2), "audit_records": audit_lines}
    except Exception as exc:  # noqa: BLE001
        out["sessions"] = {"ok": False, "error": str(exc)}

    # 3) Đăng bạ kho luật — trạng thái phê duyệt + hàm băm của từng văn bản.
    try:
        from app.domain.regulations import corpus  # noqa: PLC0415

        audit = corpus.audit_corpus()
        out["corpus"] = {"ok": not audit["blocking"], "counts": audit["counts"],
                         "fingerprint": audit["fingerprint"],
                         "documents": len(audit["documents"])}
    except Exception as exc:  # noqa: BLE001
        out["corpus"] = {"ok": False, "error": str(exc)}

    # 4) File cấu hình mặc định + cấu hình người dùng đang áp dụng.
    files = _admin_list()
    out["configs"] = {
        "ok": True,
        "by_group": {g: sum(1 for f in files if f["group"] == g) for g in _ADMIN_DIRS},
        "user_configs": user_config_list(),
        "applied": applied_config_ids(),
    }
    return out


@router.get("/file")
def admin_read(rel: str):
    """Nội dung thô một file cấu hình/luật. `rel` đi qua `_admin_resolve` để chặn
    đường dẫn vượt thư mục (`../`) và mọi đuôi ngoài .json/.md."""
    p = _admin_resolve(rel)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file")
    return {"rel": rel, "content": p.read_text(encoding="utf-8")}


@router.put("/file")
def admin_write(body: dict = Body(...)):
    """Ghi đè một file cấu hình/luật.

    Hai lớp chặn trước khi ghi: `_admin_resolve` (đường dẫn hợp lệ) và với .json là
    phải PARSE ĐƯỢC — file cấu hình hỏng cú pháp sẽ làm chết pipeline ở tận lượt chạy
    sau, rất xa chỗ gây ra.

    File .md trong `rules/` VÀ đăng bạ `rules/corpus.json` thì SEED LẠI ChromaDB ngay:
    sửa luật (hoặc sửa ngày hiệu lực trong đăng bạ) mà quên seed thì hệ vẫn đối chiếu
    theo bản cũ, và không có dấu hiệu nào cho thấy điều đó."""
    rel = str(body.get("rel", ""))
    content = str(body.get("content", ""))
    p = _admin_resolve(rel)
    if p.suffix == ".json":  # bắt buộc JSON hợp lệ mới cho ghi
        try:
            json.loads(content)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"JSON không hợp lệ: {exc}") from exc
    p.write_text(content, encoding="utf-8")

    note = "Đã lưu."
    reseeded = False
    # Sửa file LUẬT (.md) HOẶC ĐĂNG BẠ (rules/corpus.json) -> tự động NẠP LẠI ChromaDB
    # ngay trên web (khỏi vào terminal).
    #
    # Đăng bạ phải nằm trong danh sách này: `effective_from`, `effective_to`, `doc_type`
    # của mỗi văn bản được gắn vào metadata TỪNG ĐOẠN LÚC NẠP, và chính chúng là thứ bộ
    # lọc ngày ký so sánh. Sửa ngày hiệu lực mà không nạp lại thì kho vẫn lọc theo mốc
    # cũ — hồ sơ được đối chiếu với đúng cái phiên bản luật vừa bị sửa đi.
    _is_rule_md = rel.startswith("rules/") and p.suffix == ".md"
    _is_registry = rel.replace("\\", "/") == "rules/corpus.json"
    if _is_rule_md or _is_registry:
        try:
            from app.domain.regulations import seed  # noqa: PLC0415
            seed()  # reset + nạp lại toàn bộ luật (idempotent)
            reseeded = True
            note = "Đã lưu lại thay đổi."
        except Exception as exc:  # noqa: BLE001
            note = f"Đã lưu file, nhưng nạp lại kho quy định lỗi: {exc}"
    return {"ok": True, "rel": rel, "reseeded": reseeded, "note": note}
