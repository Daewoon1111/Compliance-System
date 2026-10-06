"""HẠ TẦNG (store.config) — nạp cấu hình và prompt từ file, không nhúng trong code.

Ba nguồn, xếp chồng theo thứ tự ưu tiên:
  1. cấu hình NGƯỜI DÙNG đang áp dụng (data/user_config/) — chồng lên mặc định;
  2. bộ trường 3 TẦNG khu vực -> quốc gia -> công việc (prompts/jobs/regions/...);
  3. cấu hình DỊCH VỤ (prompts/services/): extraction · validation · checks.

`prompts/` KHÔNG bao giờ bị ghi đè bởi cấu hình người dùng; gỡ áp dụng là hệ quay
về mặc định ngay.
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .paths import DATA_DIR, JOBS_DIR, MARKETS_FILE, SERVICES_DIR, read_json, write_json

# ---------------------------------------------------------------------------
# CẤU HÌNH NGƯỜI DÙNG (trang Cấu hình) — CHỒNG LÊN cấu hình mặc định khi được ÁP DỤNG.
# ---------------------------------------------------------------------------
USER_CONFIG_DIR = DATA_DIR / "user_config"
USER_CONFIG_DIRS: dict[str, Path] = {
    "jobs": USER_CONFIG_DIR / "jobs",
    "markets": USER_CONFIG_DIR / "markets",
}
APPLIED_FILE = USER_CONFIG_DIR / "applied.json"


@lru_cache(maxsize=256)
def _json_at(path: Path, _mtime_ns: int) -> Any:
    """Đọc JSON tại đường dẫn; thiếu file hoặc hỏng -> `{}` (không chặn cả hệ)."""
    return read_json(path)


def cached_json(path: Path) -> Any:
    """Đọc JSON có NHỚ ĐỆM theo mtime — sửa file là tự nạp lại, không cần khởi động lại.

    Dựng bộ trường cho một lựa chọn phải mở lại markets.json + 3 file tầng; trang
    kiểm tra gọi việc này cho từng tài liệu, từng lần đối chiếu. Đọc + parse lại vài
    chục KB JSON mỗi lần là chi phí thuần túy vô ích: nội dung chỉ đổi khi có người
    sửa file, và mtime nói đúng lúc đó."""
    return _json_at(path, path.stat().st_mtime_ns)


def applied_config_ids() -> dict[str, list[str]]:
    """{'jobs': [...], 'markets': [...]} — các cấu hình người dùng đang có hiệu lực.

    Được hỏi 4 lần cho MỖI lần dựng bộ trường (3 tầng + danh mục) nên đọc qua bộ nhớ
    đệm theo mtime; `set_config_applied` ghi file là cache tự hết hạn."""
    try:
        data = cached_json(APPLIED_FILE)
    except Exception:  # noqa: BLE001 - thiếu/hỏng file không được chặn cả hệ
        data = {}
    return {k: [str(x) for x in (data.get(k) or [])] for k in USER_CONFIG_DIRS}


def set_config_applied(kind: str, config_id: str, on: bool) -> None:
    """Đánh dấu cấu hình người dùng `cid` là ĐANG ÁP DỤNG (hoặc gỡ khi `on=False`)."""
    data = applied_config_ids()
    data[kind] = [i for i in data.get(kind, []) if i != config_id] + ([config_id] if on else [])
    write_json(APPLIED_FILE, data)


def _user_config_path(kind: str, config_id: str) -> Path:
    """Đường dẫn file cấu hình người dùng theo (nhóm, mã)."""
    return USER_CONFIG_DIRS[kind] / f"{config_id}.json"


def read_user_config(kind: str, config_id: str) -> str | None:
    """Nội dung một cấu hình người dùng; chưa có -> `{}`."""
    p = _user_config_path(kind, config_id)
    return p.read_text(encoding="utf-8") if p.exists() else None


def write_user_config(kind: str, config_id: str, content: str) -> None:
    """Ghi đè một cấu hình người dùng và trả về đường dẫn đã ghi."""
    p = _user_config_path(kind, config_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def user_config_list() -> list[dict[str, Any]]:
    """Danh sách cấu hình người dùng đã tạo (kèm tên hiển thị + trạng thái áp dụng)."""
    applied = applied_config_ids()
    out: list[dict[str, Any]] = []
    for kind, base in USER_CONFIG_DIRS.items():
        for p in sorted(base.glob("*.json")) if base.exists() else []:
            try:
                data = read_json(p)
            except Exception:  # noqa: BLE001
                data = {}
            out.append({
                "kind": kind,
                "id": p.stem,
                "display": data.get("display_name") or data.get("name") or p.stem,
                "applied": p.stem in applied.get(kind, []),
            })
    return out


def default_config_ids(kind: str) -> set[str]:
    """Mã của cấu hình MẶC ĐỊNH (tệp tầng bộ trường / thị trường trong markets.json).

    Cấu hình người dùng không cần mã quản trị, nên KHÔNG được mang trùng các mã này:
    trùng mã là thay trọn một tầng mặc định (bỏ luôn trường bắt buộc Điều 19), tức vượt
    quyền trang quản trị."""
    if kind == "jobs":
        ids = {p.stem for d in (REGIONS_DIR, COUNTRIES_DIR, WORKS_DIR) if d.exists()
               for p in d.glob("*.json")}
        ids |= {p.stem for p in JOBS_DIR.glob("*.json") if p.name != MARKETS_FILE.name}
        return ids
    try:
        return {str(m.get("id")) for m in (cached_json(MARKETS_FILE).get("markets") or [])}
    except Exception:  # noqa: BLE001
        return set()


def _applied_user_json(kind: str, config_id: str) -> dict[str, Any] | None:
    """Nội dung cấu hình người dùng CHỈ KHI đang được áp dụng và KHÔNG trùng mã mặc định."""
    if config_id not in applied_config_ids().get(kind, []):
        return None
    if config_id in default_config_ids(kind):
        print(f"[config] bỏ qua cấu hình người dùng {kind}/{config_id}: trùng mã mặc định.")
        return None
    p = _user_config_path(kind, config_id)
    if not p.exists():
        return None
    try:
        return read_json(p)
    except Exception as exc:  # noqa: BLE001
        print(f"[config] cấu hình người dùng {kind}/{config_id}.json hỏng ({exc}) -> dùng mặc định.")
        return None


# ---------------------------------------------------------------------------
# BỘ TRƯỜNG THEO 3 TẦNG: KHU VỰC -> QUỐC GIA -> CÔNG VIỆC
#
# Ba tầng nằm trong THƯ MỤC LỒNG NHAU đúng theo thứ tự chồng tầng, nên nhìn cây thư
# mục là biết ngay tầng nào chồng lên tầng nào. Phần dùng chung chỉ khai MỘT chỗ:
#   jobs/regions/<region_id>.json                       nền chung của cả khu vực
#   jobs/regions/countries/<market_id>.json             CHỈ phần khác với khu vực
#   jobs/regions/countries/works/<job_type_id>.json     CHỈ phần riêng của loại hình
# Tầng sau CHỒNG LÊN tầng trước theo từng khóa (kể cả từng khóa con của mỗi trường
# trong fields_catalog), nên "công việc trên biển" là một tầng CÔNG VIỆC dùng được ở
# mọi khu vực, không còn là một "thị trường" giả.
# ---------------------------------------------------------------------------
REGIONS_DIR = JOBS_DIR / "regions"
COUNTRIES_DIR = REGIONS_DIR / "countries"
WORKS_DIR = COUNTRIES_DIR / "works"
JOB_LAYER_DIRS = {"regions": REGIONS_DIR, "countries": COUNTRIES_DIR, "works": WORKS_DIR}

# MÃ tầng / bộ trường đi thẳng từ form tải lên (`job_type`, `job_id`) vào tên tệp. Chỉ
# nhận chữ/số/gạch dưới/gạch nối (kể cả chữ có dấu — `_safe_id` của cấu hình người
# dùng giữ chúng). Không chặn thì `job_type="/tmp/x"` hay `"../../services/checks"`
# mở được tệp `.json` bất kỳ trên đĩa làm bộ trường — vì `Path / "/tuyệt/đối"` bỏ
# hẳn thư mục gốc, và trên Windows cả `\` lẫn `C:` cũng thoát ra ngoài.
_LAYER_ID_RX = re.compile(r"\A[\w-]+\Z")


def valid_layer_id(key: str) -> bool:
    """`key` có phải một mã tầng/bộ trường dùng làm tên tệp được không."""
    return bool(_LAYER_ID_RX.match(key or ""))

# TẦNG NỀN, dưới cả tầng khu vực: danh mục 50 trường dùng chung cho MỌI khu vực.
# Trước đó tám file khu vực chép trọn cùng một danh mục — 48/50 trường giống hệt nhau
# tới từng ký tự. Hệ quả không phải là tốn chỗ mà là im lặng: sửa một nhãn phải sửa
# tám chỗ, bỏ sót một chỗ thì đúng một thị trường chạy theo bản cũ và không có gì báo.
BASE_LAYER_ID = "_base"
BASE_LAYER_FILE = REGIONS_DIR / f"{BASE_LAYER_ID}.json"


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Gộp `over` lên `base`. Dict lồng nhau gộp đệ quy; list/giá trị thường thay thế
    hẳn (danh sách always_check của tầng sau là bản CUỐI, không cộng dồn nửa vời).

    Kết quả KHÔNG dùng chung object con với `base`/`over`: các tầng nay đến từ bộ nhớ
    đệm (`cached_json`), nơi gọi mà lỡ sửa bộ trường sẽ làm hỏng cache cho mọi phiên
    sau."""
    out = dict(base)
    for k, v in over.items():
        cur = out.get(k)
        if isinstance(cur, dict) and isinstance(v, dict):
            out[k] = _deep_merge(cur, v)
        else:
            out[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    return out


def _with_base(layer: dict[str, Any]) -> dict[str, Any]:
    """Chồng một bộ trường người dùng lên NỀN CHUNG (`_base`) — không thay nền."""
    try:
        return _deep_merge(cached_json(BASE_LAYER_FILE), layer) if BASE_LAYER_FILE.exists() else layer
    except Exception:  # noqa: BLE001
        return layer


def region_of(market_id: str, country_id: str = "") -> str:
    """Khu vực của một lựa chọn. Ưu tiên khu vực của ĐÚNG quốc gia đã chọn (một thị
    trường như 'tay_a_trung_a_chau_phi' trải trên nhiều khu vực), sau đó mới tới
    `region_id` khai thẳng ở thị trường (vd Biển quốc tế — không có quốc gia)."""
    mkt = resolve_market(market_id)
    if not mkt:
        return ""
    countries = mkt.get("countries") or []
    if country_id:
        for c in countries:
            if c.get("id") == country_id:
                return str(c.get("region_id") or "")
    if mkt.get("region_id"):
        return str(mkt["region_id"])
    return str(countries[0].get("region_id") or "") if countries else ""


def _layer(kind: str, key: str) -> dict[str, Any] | None:
    """Một TẦNG bộ trường (khu vực / quốc gia / công việc), đã chồng cấu hình người dùng.

    Ba tầng chồng lên nhau theo thứ tự khu vực -> quốc gia -> công việc; tầng sau ghi
    đè khóa trùng của tầng trước. Cả ba tầng đi qua CÙNG một đường nạp ở đây, nên
    cấu hình người dùng chồng lên tầng nào cũng có hiệu lực như nhau."""
    if not valid_layer_id(key):
        return None
    user = _applied_user_json("jobs", key)   # tầng MỚI do người dùng khai
    if user is not None:
        return _with_base(user) if kind == "regions" else user
    p = JOB_LAYER_DIRS[kind] / f"{key}.json"
    if not p.exists():
        return None
    try:
        layer = cached_json(p)
    except Exception as exc:  # noqa: BLE001 - một tầng hỏng không được chặn cả hệ
        print(f"[config] tầng {kind}/{key}.json hỏng ({exc}) -> bỏ qua tầng này.")
        return None
    # Tầng khu vực chồng lên NỀN CHUNG. Ghép ở đây chứ không ở `resolve_job_prompt` để
    # mọi đường vào (kể cả `load_job_prompt` với một mã khu vực) đều thấy cùng một bộ.
    if kind == "regions" and key != BASE_LAYER_ID and BASE_LAYER_FILE.exists():
        try:
            return _deep_merge(cached_json(BASE_LAYER_FILE), layer)
        except Exception as exc:  # noqa: BLE001
            print(f"[config] nền chung _base.json hỏng ({exc}) -> chỉ dùng tầng khu vực.")
    return layer


def load_job_prompt(job_id: str) -> dict[str, Any]:
    """Bộ trường ĐẦY ĐỦ theo MỘT khóa đơn (tương thích ngược + cấu hình người dùng).

    QUAN TRỌNG: file trong `countries/` chỉ là phần KHÁC so với khu vực, nên với khóa
    quốc gia phải GHÉP tầng khu vực vào mới ra bộ trường đủ. Thứ tự tìm: cấu hình
    người dùng đang áp dụng -> quốc gia (ghép khu vực) -> khu vực -> công việc ->
    file phẳng jobs/<id>.json (bộ cũ, nếu còn)."""
    if not valid_layer_id(job_id):
        raise FileNotFoundError(f"Unknown job_id: {job_id!r}")
    user = _applied_user_json("jobs", job_id)
    if user is not None:
        return _with_base(user)
    if (COUNTRIES_DIR / f"{job_id}.json").exists():
        return resolve_job_prompt(job_id, "", "", job_id)
    # Mã khu vực đi qua `_layer` để nhận NỀN CHUNG; hai thư mục còn lại không có nền.
    if (REGIONS_DIR / f"{job_id}.json").exists():
        return copy.deepcopy(_layer("regions", job_id) or {})
    for base in (WORKS_DIR, JOBS_DIR):
        p = base / f"{job_id}.json"
        if p.exists():
            return copy.deepcopy(cached_json(p))
    raise FileNotFoundError(f"Unknown job_id: {job_id}")


def resolve_job_prompt(
    market_id: str, country_id: str = "", job_type_id: str = "", fallback_job_id: str = "",
) -> dict[str, Any]:
    """Dựng bộ trường cho một lựa chọn (khu vực + quốc gia + loại hình lao động).

    Không tầng nào khớp -> lùi về `fallback_job_id` (file phẳng/cấu hình người dùng)
    để hệ thống vẫn chạy khi cấu hình chưa đủ."""
    layers = [x for x in (
        _layer("regions", region_of(market_id, country_id)),
        _layer("countries", market_id),
        _layer("works", job_type_id),
    ) if x]
    if not layers:
        return load_job_prompt(fallback_job_id or market_id)
    out: dict[str, Any] = {}
    for layer in layers:
        out = _deep_merge(out, layer)
    # `notes` của từng tầng là chú thích nội bộ về cách xếp tầng -> không gửi đi đâu.
    out.pop("notes", None)
    out.setdefault("job_id", fallback_job_id or market_id)
    return out


def load_markets() -> dict[str, Any]:
    """Khu vực + thị trường + quốc gia + loại hình lao động (3 nút chọn ở trang 1).

    Thị trường do người dùng tạo và ĐÃ ÁP DỤNG được ghép thêm vào danh sách; trùng
    `id` với thị trường mặc định thì bản của người dùng THAY THẾ bản mặc định."""
    cfg = cached_json(MARKETS_FILE)
    ids = applied_config_ids().get("markets", [])
    if not ids:
        return cfg
    markets = list(cfg.get("markets") or [])
    regions = list(cfg.get("regions") or [])
    known_regions = {r.get("id") for r in regions}
    for cid in ids:
        m = _applied_user_json("markets", cid)
        if not m:
            continue
        markets = [x for x in markets if x.get("id") != m.get("id")]
        markets.append(m)
        # Khu vực mới do người dùng khai (region_id chưa có) -> tự thêm để trang 1 hiện.
        for rid in {m.get("region_id")} | {c.get("region_id") for c in (m.get("countries") or [])}:
            if rid and rid not in known_regions:
                known_regions.add(rid)
                regions.append({"id": rid, "name": rid})
    return {**cfg, "markets": markets, "regions": regions}


def resolve_market(market_id: str) -> dict[str, Any] | None:
    """Thông tin thị trường theo mã; không có -> `{}`."""
    return next((m for m in load_markets().get("markets", []) if m.get("id") == market_id), None)


def resolve_country(market: dict[str, Any] | None, country_id: str) -> dict[str, Any] | None:
    """QUỐC GIA/VÙNG LÃNH THỔ trong một thị trường (dùng tên + keywords đối chiếu chéo)."""
    if not market or not country_id:
        return None
    return next((c for c in (market.get("countries") or []) if c.get("id") == country_id), None)


def is_meaningful_text(s: str, min_len: int = 6, min_words: int = 2) -> bool:
    """Kiểm soát ô nhập 'khác': chống nhập bừa để LLM còn hiểu.

    Yêu cầu: đủ dài, có >=`min_words` 'từ' chứa chữ/số, có ký tự chữ (kể cả tiếng
    Việt), và KHÔNG phải một ký tự lặp lại (vd 'aaaaaa', '111111')."""
    t = (s or "").strip()
    if len(t) < min_len or not re.search(r"[A-Za-zÀ-ỹà-ỹ]", t):
        return False
    if len([w for w in t.split() if re.search(r"[A-Za-zÀ-ỹ0-9]", w)]) < min_words:
        return False
    return len(set(re.sub(r"\s+", "", t))) > 2   # chống lặp 1 ký tự: 'aaaa', 'a a a a'


# ---------------------------------------------------------------------------
# CẤU HÌNH DỊCH VỤ (prompts/services/) — chỉ sửa trực tiếp trên file, không qua web,
# nên nạp MỘT LẦN rồi cache; đổi file thì khởi động lại backend (hoặc cache_clear()).
# ---------------------------------------------------------------------------
def load_extraction_config() -> dict[str, Any]:
    """`prompts/services/extraction.json` — rule trích xuất bằng regex/nhãn."""
    return _service_json("extraction.json")


def load_validation_prompt() -> dict[str, Any]:
    """`prompts/services/validation_prompt.json` — prompt + schema bước đối chiếu."""
    return _service_json("validation_prompt.json")


def load_extraction_llm_prompt() -> dict[str, Any]:
    """`prompts/services/extraction_llm.json` — prompt bước LLM bù trường còn thiếu."""
    return _service_json("extraction_llm.json")


def _service_json(name: str) -> dict[str, Any]:
    """Đọc một tệp cấu hình DỊCH VỤ trong `prompts/services/`, nhớ đệm theo mtime.

    Bước trích xuất hỏi `extraction.json` hàng chục lần cho mỗi tài liệu; đọc và phân
    tích lại vài chục KB JSON mỗi lần là chi phí thuần túy vô ích. Sửa tệp vẫn có hiệu
    lực ngay vì khóa đệm gồm mtime."""
    return cached_json(SERVICES_DIR / name)


@lru_cache(maxsize=1)
def _load_checks() -> dict[str, Any]:
    """Đọc cấu hình kiểm tra hợp nhất (checks.json). Thiếu/hỏng -> {} + KÊU TO.

    Nuốt im lặng ở đây là nguy hiểm nhất trong cả hệ: `{}` nghĩa là MẤT SẠCH luật
    kiểm tra tất định + danh sách khoản thu bị cấm, hệ vẫn chạy và vẫn ra kết luận —
    chỉ là kết luận thiếu căn cứ. Sai một dấu phẩy JSON không được phép âm thầm biến
    hệ kiểm tra thành hệ không kiểm tra gì."""
    path = SERVICES_DIR / "checks.json"
    if not path.exists():
        print(f"[config] THIẾU {path.name} -> bỏ qua toàn bộ kiểm tra tất định "
              "và danh mục khoản thu bị cấm.")
        return {}
    try:
        return read_json(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[config] {path.name} KHÔNG đọc được ({exc}) -> bỏ qua toàn bộ kiểm tra "
              "tất định và danh mục khoản thu bị cấm. Sửa file rồi khởi động lại.")
        return {}


def checks_config_ok() -> bool:
    """checks.json đọc được và có nội dung (điều kiện để chạy bước kiểm tra)."""
    return bool(_load_checks())


def load_checks_section(name: str) -> dict[str, Any]:
    """Một mục bất kỳ trong checks.json (vd 'payer_cost')."""
    return _load_checks().get(name) or {}


def load_factual_rules() -> dict[str, Any]:
    """Cấu hình kiểm tra tất định (checks.json > factual)."""
    return load_checks_section("factual")


def load_dossier_rules() -> dict[str, Any]:
    """Cấu hình phân tích bộ hồ sơ (checks.json > dossier)."""
    return load_checks_section("dossier")


def load_input_quality_config() -> dict[str, Any]:
    """Cấu hình kiểm soát chất lượng đầu vào (checks.json > input_quality)."""
    return load_checks_section("input_quality")


def load_playbook() -> dict[str, Any]:
    """PLAYBOOK tuân thủ (Tầng 3.1): mức rủi ro + điều luật + mẫu sửa theo check_id,
    default + ghi đè theo thị trường (checks.json > playbook)."""
    return load_checks_section("playbook")


def load_legal_basis() -> dict[str, list[dict[str, Any]]]:
    """CĂN CỨ PHÁP LÝ CỐ ĐỊNH cho các kết luận không do LLM sinh (ký quỹ, khoản thu
    bị cấm, giữ giấy tờ tùy thân). Khai trong checks.json > legal_basis:
    {"<mã cờ>": [{source_doc, text_quote, doc_type, effective_from}]}."""
    return load_checks_section("legal_basis")


# ---------------------------------------------------------------------------
# fields_catalog: hỗ trợ cả định dạng cũ (key -> "mô tả") lẫn mới
# (key -> {"label", "fill_hint", "check_aspect"}).
# ---------------------------------------------------------------------------
def _field_attr(entry: Any, first: str, second: str, fallback: str) -> str:
    """Thuộc tính `attr` của một mục catalog, chịu được cả hai dạng khai báo.

    Mục có thể là dict (`{"label": ...}`) hoặc chỉ là một chuỗi tên trường — dạng thứ
    hai xuất hiện ở các bộ trường viết tắt, và nếu không xử lý thì `.get()` ném
    AttributeError giữa lượt kiểm tra."""
    if isinstance(entry, dict):
        return entry.get(first) or entry.get(second) or fallback
    return str(entry) if entry else fallback


def field_label(entry: Any, fallback: str = "") -> str:
    """Tên ngắn của trường (cho UI title, dựng truy vấn RAG, tiêu đề check)."""
    return _field_attr(entry, "label", "check_aspect", fallback)


def field_check_aspect(entry: Any, fallback: str = "") -> str:
    """Tiêu chí kiểm tra (cái gì hợp lệ/vi phạm) — lái RAG và LLM."""
    return _field_attr(entry, "check_aspect", "label", fallback)


def slim_fields_catalog(fields_catalog: dict[str, Any]) -> dict[str, Any]:
    """Bỏ 'fill_hint' (hướng dẫn nhập liệu), chỉ giữ 'label' + 'check_aspect' để
    gửi cho LLM: tiết kiệm token và loại nhiễu hướng dẫn nhập liệu khỏi suy luận."""
    return {
        k: ({"label": v.get("label", k), "check_aspect": v.get("check_aspect", "")}
            if isinstance(v, dict) else {"label": str(v), "check_aspect": str(v)})
        for k, v in (fields_catalog or {}).items()
    }
