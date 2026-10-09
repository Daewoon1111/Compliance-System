"""TẦNG API (admin) — TRANG QUẢN TRỊ (cần mã quản trị): sửa cấu hình MẶC ĐỊNH của hệ thống.

Cho sửa và tạo mới: bộ trường mặc định (field_sets) và văn bản quy định (rules) — lưu
văn bản quy định thì tự nạp lại ChromaDB. Kèm endpoint KIỂM TRA DATABASE (kho quy
định ChromaDB + dữ liệu phiên) để quản trị viên biết hệ đang ở trạng thái nào.

Cấu hình DỊCH VỤ (extraction/validation/checks) vẫn chỉ sửa trực tiếp trên file.
Bộ trường của người dùng nằm ở router `config.py` (không cần mã quản trị).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from app.core import settings
from app.store import list_field_sets, technical_metrics


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
_ADMIN_DIRS = {
    "field_sets": APP_DIR / "prompts" / "field_sets",
    "rules": APP_DIR / "rules",
}
# Đuôi file được phép theo nhóm: bộ trường là JSON; thư mục quy định có văn bản .md và
# đúng một tệp JSON là đăng bạ corpus.json.
_ALLOWED_SUFFIX = {"field_sets": (".json",), "rules": (".md", ".json")}


def _admin_display(grp: str, p) -> str:
    """Tên hiển thị TIẾNG VIỆT CÓ DẤU cho từng file cấu hình."""
    try:
        if grp == "field_sets":
            return json.loads(p.read_text(encoding="utf-8")).get("display_name", p.stem)
        if grp == "rules" and p.suffix == ".json":
            return "Đăng bạ kho quy định"
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
        for p in sorted(base.iterdir()):
            if not p.is_file() or p.suffix not in _ALLOWED_SUFFIX[grp]:
                continue
            if grp == "rules" and p.suffix == ".json" and p.name != "corpus.json":
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
    if base is None or not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ")
    p = (base / name).resolve()
    if base.resolve() not in p.parents or p.suffix not in _ALLOWED_SUFFIX[grp]:
        raise HTTPException(status_code=400, detail="Không được phép sửa file này")
    if grp == "rules" and p.suffix == ".json" and p.name != "corpus.json":
        raise HTTPException(status_code=400, detail="Không được phép sửa file này")
    return p


@router.get("/files")
def admin_files():
    """Cây file cấu hình + văn bản quy định sửa được trên trang quản trị."""
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
    """QUẢN TRỊ KHO QUY ĐỊNH: từng văn bản kèm nguồn · phiên bản · hiệu lực · hàm băm · người duyệt.

    Đối chiếu đăng bạ `rules/corpus.json` với file thật trên đĩa. Trả kèm VÂN TAY của
    cả kho — chính con số nằm trong chữ ký yêu cầu kiểm tra, nên đổi kho là mọi báo cáo
    lưu sẵn tự động phải chạy lại."""
    from app.domain.regulations import corpus  # noqa: PLC0415 — nạp trễ, tránh vòng import

    corpus.sync_registry()   # file mới thêm tay vào rules/ vẫn hiện ra, ở trạng thái chưa khai
    return corpus.audit_corpus()


@router.post("/corpus/approve")
def admin_corpus_approve(body: dict = Body(...)):
    """PHÊ DUYỆT một văn bản quy định: chốt hàm băm hiện tại + ghi người duyệt và thời điểm.

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

    # 1) Kho quy định ChromaDB — số đoạn quy định đã nạp, theo từng văn bản nguồn.
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
            "warning": "" if total else "Kho quy định TRỐNG — thêm văn bản quy định (.md) rồi chạy `npm run seed` hoặc lưu văn bản trên trang này.",
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

    # 3) Đăng bạ kho quy định — trạng thái phê duyệt + hàm băm của từng văn bản.
    try:
        from app.domain.regulations import corpus  # noqa: PLC0415

        audit = corpus.audit_corpus()
        out["corpus"] = {"ok": not audit["blocking"], "counts": audit["counts"],
                         "fingerprint": audit["fingerprint"],
                         "documents": len(audit["documents"])}
    except Exception as exc:  # noqa: BLE001
        out["corpus"] = {"ok": False, "error": str(exc)}

    # 4) File cấu hình mặc định + bộ trường hiện có.
    files = _admin_list()
    out["configs"] = {
        "ok": True,
        "by_group": {g: sum(1 for f in files if f["group"] == g) for g in _ADMIN_DIRS},
        "field_sets": list_field_sets(),
    }
    return out


@router.get("/file")
def admin_read(rel: str):
    """Nội dung thô một file cấu hình/quy định. `rel` đi qua `_admin_resolve` để chặn
    đường dẫn vượt thư mục (`../`) và mọi đuôi không được phép."""
    p = _admin_resolve(rel)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file")
    return {"rel": rel, "content": p.read_text(encoding="utf-8")}


@router.put("/file")
def admin_write(body: dict = Body(...)):
    """Ghi (tạo mới hoặc ghi đè) một file cấu hình/quy định.

    Chặn trước khi ghi: `_admin_resolve` (đường dẫn hợp lệ), .json phải PARSE ĐƯỢC, và
    bộ trường phải HỢP LỆ — file hỏng sẽ làm chết pipeline ở tận lượt chạy sau.

    File .md trong `rules/` VÀ đăng bạ `rules/corpus.json` thì NẠP LẠI ChromaDB ngay:
    sửa văn bản (hoặc ngày hiệu lực trong đăng bạ) mà quên nạp thì hệ vẫn đối chiếu
    theo bản cũ, và không có dấu hiệu nào cho thấy điều đó."""
    rel = str(body.get("rel", ""))
    content = str(body.get("content", ""))
    p = _admin_resolve(rel)
    if p.suffix == ".json":  # bắt buộc JSON hợp lệ mới cho ghi
        try:
            data = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"JSON không hợp lệ: {exc}") from exc
        if rel.startswith("field_sets/"):
            from app.store import field_set_problems  # noqa: PLC0415

            if problems := field_set_problems(data):
                raise HTTPException(status_code=400, detail=" ".join(problems))
    if not content.strip() and p.suffix == ".md":
        raise HTTPException(status_code=400, detail="Văn bản quy định đang trống.")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

    note = "Đã lưu."
    reseeded = False
    # Sửa VĂN BẢN QUY ĐỊNH (.md) HOẶC ĐĂNG BẠ -> tự động NẠP LẠI ChromaDB. Đăng bạ phải
    # nằm trong danh sách này: hiệu lực và loại văn bản được gắn vào metadata TỪNG ĐOẠN
    # LÚC NẠP, và chính chúng là thứ bộ lọc ngày ký so sánh.
    _is_rule_md = rel.startswith("rules/") and p.suffix == ".md"
    _is_registry = rel.replace("\\", "/") == "rules/corpus.json"
    if _is_rule_md or _is_registry:
        try:
            from app.domain.documents.spelling import reset_lexicon  # noqa: PLC0415
            from app.domain.regulations import seed  # noqa: PLC0415
            seed()  # reset + nạp lại toàn bộ quy định (idempotent)
            reset_lexicon()   # từ điển khôi phục dấu dựng từ chính kho quy định
            reseeded = True
            note = "Đã lưu lại thay đổi."
        except Exception as exc:  # noqa: BLE001
            note = f"Đã lưu file, nhưng nạp lại kho quy định lỗi: {exc}"
    return {"ok": True, "rel": rel, "reseeded": reseeded, "note": note}
