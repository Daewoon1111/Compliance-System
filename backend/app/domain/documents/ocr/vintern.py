"""ĐỌC HỒ SƠ · ocr.vintern — VÒNG ĐỜI mô hình Vintern-1B-v3.5 (5CD-AI) và lượt đọc một ảnh.

Vintern là mô hình thị giác-ngôn ngữ (InternVL2.5-1B tinh chỉnh cho tiếng Việt), không
phải OCR cổ điển: nó KHÔNG trả hộp tọa độ hay độ tin cậy từng dòng. Module này bù hai thứ
đó ở mức đủ dùng cho hệ thống:

  · ĐỘ TIN CẬY: tính từ xác suất của từng token mô hình sinh ra (giải mã tham lam), gom
    theo dòng. Cổng chất lượng OCR (`quality._ocr_flags`) và phần tô dòng đáng ngờ ở
    trang soát vẫn đọc cùng một con số `conf` như trước.
  · THỨ TỰ ĐỌC: mô hình tự chép theo thứ tự đọc, nên không còn bước sắp hộp theo cột.

Ba chốt chặn vì mô hình sinh chữ có thể BỊA:
  1. Giải mã tham lam, phạt lặp NHẸ (`vintern_repetition_penalty`). Mức 2,5 trong ví dụ
     của model card phạt cả những cụm số lặp thật ("1.000.000") — đổi nội dung số tiền.
  2. Cắt vòng lặp: một dòng lặp lại liên tiếp từ lần thứ `_MAX_REPEAT` thì bỏ phần lặp.
  3. Chạm trần token (trang dày) thì chia đôi ảnh và đọc từng nửa, không nhận bản cụt.

`trust_remote_code=True` là BẮT BUỘC với kiến trúc InternVL, nên REVISION luôn được ghim
(`vintern_revision`): không ghim thì một lần cập nhật mã trên Hugging Face tự chạy trên máy.
"""
from __future__ import annotations

import sys
import threading
from typing import Any

from app.core import settings

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_INPUT_SIZE = 448
_MAX_REPEAT = 3
# Byte-level BPE (Qwen2) mã hóa '\n' thành ký tự 'Ċ' (U+010A) trong chuỗi token.
_BPE_NEWLINE = "Ċ"

OCR_PROMPT = (
    "<image>\nChép lại NGUYÊN VĂN toàn bộ chữ tiếng Việt trong ảnh theo đúng thứ tự đọc, "
    "mỗi dòng văn bản trên một dòng. Không tóm tắt, không giải thích, không sửa chính tả, "
    "không thêm nội dung không có trong ảnh. Giữ nguyên chữ số, dấu chấm và dấu phẩy "
    "trong số tiền. Bảng thì mỗi hàng một dòng, các ô cách nhau bằng ' | '."
)

_STATE: dict[str, Any] = {"model": None, "tokenizer": None, "device": None, "dtype": None}
_LOCK = threading.Lock()
_LOAD_ERROR: str | None = None


class OcrUnavailableError(RuntimeError):
    """Không nạp được Vintern (thiếu gói, chưa tải được model, hết bộ nhớ...)."""


# ---------------------------------------------------------------------------
# Nạp model
# ---------------------------------------------------------------------------
def _pick_device(torch) -> tuple[str, Any]:
    """(thiết bị, dtype) theo `vintern_device`: auto | cuda | cpu."""
    want = (settings.vintern_device or "auto").strip().lower()
    has_cuda = bool(torch.cuda.is_available())
    if want == "cuda" and not has_cuda:
        raise OcrUnavailableError("vintern_device=cuda nhưng máy không có CUDA khả dụng.")
    if want == "cuda" or (want == "auto" and has_cuda):
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        return "cuda", dtype
    # bfloat16 trên CPU chạy được nhưng nhiều phép toán chậm hẳn; float32 ổn định hơn.
    return "cpu", torch.float32


def _load_model():
    """Nạp model + tokenizer đúng một lần cho cả tiến trình (có khóa)."""
    global _LOAD_ERROR
    with _LOCK:
        if _STATE["model"] is not None:
            return _STATE
        try:
            import torch  # noqa: PLC0415 — nạp trễ, chỉ khi thật sự đọc ảnh
            from transformers import AutoModel, AutoTokenizer  # noqa: PLC0415

            device, dtype = _pick_device(torch)
            kw = {"revision": settings.vintern_revision or None, "trust_remote_code": True}
            # use_safetensors=True: KHÔNG bao giờ đi qua `torch.load`. torch 2.5.1
            # dính CVE-2025-32434 (nạp checkpoint .bin chạy được mã tùy ý, kể cả với
            # weights_only=True). Kho Vintern chỉ có `model.safetensors`, nên ràng
            # buộc này không mất gì mà đóng hẳn đường .bin nếu kho bị đổi nội dung.
            model = AutoModel.from_pretrained(
                settings.vintern_model, torch_dtype=dtype, low_cpu_mem_usage=True,
                use_flash_attn=False, use_safetensors=True, **kw,
            ).eval().to(device)
            tokenizer = AutoTokenizer.from_pretrained(settings.vintern_model, use_fast=False, **kw)
        except OcrUnavailableError as exc:
            _LOAD_ERROR = str(exc)
            raise
        except Exception as exc:  # noqa: BLE001 — gói thiếu / tải model hỏng / hết RAM
            _LOAD_ERROR = repr(exc)
            raise OcrUnavailableError(
                f"Không nạp được mô hình OCR {settings.vintern_model}: {exc!r}. "
                "Kiểm tra: pip install -r requirements.txt; lần chạy đầu cần mạng để tải "
                "model (~1 GB); máy CPU cần khoảng 4 GB RAM trống."
            ) from exc
        _LOAD_ERROR = None
        _STATE.update(model=model, tokenizer=tokenizer, device=device, dtype=dtype)
        print(f"[ocr] Vintern sẵn sàng · {settings.vintern_model}@"
              f"{(settings.vintern_revision or 'main')[:8]} · {device} · {str(dtype).split('.')[-1]}")
        return _STATE


def get_ocr() -> dict[str, Any]:
    """Trạng thái model đã nạp. Không nạp được -> `OcrUnavailableError` kèm nguyên nhân."""
    return _load_model()


def reset_ocr() -> None:
    """Bỏ model khỏi bộ nhớ (lần đọc sau nạp lại)."""
    with _LOCK:
        _STATE.update(model=None, tokenizer=None, device=None, dtype=None)


# ---------------------------------------------------------------------------
# Ảnh -> pixel_values (theo đúng tiền xử lý của model card)
# ---------------------------------------------------------------------------
def _closest_ratio(aspect: float, ratios: list[tuple[int, int]], w: int, h: int) -> tuple[int, int]:
    best, best_diff = (1, 1), float("inf")
    area = w * h
    for r in ratios:
        diff = abs(aspect - r[0] / r[1])
        if diff < best_diff:
            best, best_diff = r, diff
        elif diff == best_diff and area > 0.5 * _INPUT_SIZE * _INPUT_SIZE * r[0] * r[1]:
            best = r
    return best


def tile_image(image, max_tiles: int) -> list:
    """Chia ảnh PIL thành các ô 448x448 theo tỉ lệ gần nhất (+ ảnh thu nhỏ toàn trang)."""
    w, h = image.size
    n = max(1, int(max_tiles))
    ratios = sorted({(i, j) for k in range(1, n + 1) for i in range(1, k + 1)
                     for j in range(1, k + 1) if 1 <= i * j <= n}, key=lambda x: x[0] * x[1])
    cols, rows = _closest_ratio(w / max(1, h), ratios, w, h)
    resized = image.resize((_INPUT_SIZE * cols, _INPUT_SIZE * rows))
    tiles = [resized.crop((c * _INPUT_SIZE, r * _INPUT_SIZE,
                           (c + 1) * _INPUT_SIZE, (r + 1) * _INPUT_SIZE))
             for r in range(rows) for c in range(cols)]
    if len(tiles) > 1:
        tiles.append(image.resize((_INPUT_SIZE, _INPUT_SIZE)))
    return tiles


def _pixel_values(image, max_tiles: int, torch, dtype, device):
    import torchvision.transforms as T  # noqa: PLC0415
    from torchvision.transforms.functional import InterpolationMode  # noqa: PLC0415

    tf = T.Compose([
        T.Resize((_INPUT_SIZE, _INPUT_SIZE), interpolation=InterpolationMode.BICUBIC),
        T.ToTensor(), T.Normalize(mean=_IMAGENET_MEAN, std=_IMAGENET_STD),
    ])
    tiles = tile_image(image.convert("RGB"), max_tiles)
    return torch.stack([tf(t) for t in tiles]).to(device=device, dtype=dtype)


def _build_query(model, tokenizer, question: str, num_patches: int) -> tuple[str, int]:
    """Prompt đúng khuôn `model.chat` (template Hermes-2) + id token kết thúc."""
    mod = sys.modules.get(type(model).__module__)
    get_tpl = getattr(mod, "get_conv_template", None)
    if get_tpl is not None:
        tpl = get_tpl(model.template)
        tpl.system_message = model.system_message
        tpl.append_message(tpl.roles[0], question)
        tpl.append_message(tpl.roles[1], None)
        query, sep = tpl.get_prompt(), tpl.sep.strip()
    else:   # khuôn Hermes-2 (SeparatorStyle.MPT) chép tay khi không truy được module
        sep = "<|im_end|>"
        query = (f"<|im_start|>system\n{model.system_message}{sep}"
                 f"<|im_start|>user\n{question}{sep}<|im_start|>assistant\n")
    image_tokens = "<img>" + "<IMG_CONTEXT>" * model.num_image_token * num_patches + "</img>"
    return query.replace("<image>", image_tokens, 1), tokenizer.convert_tokens_to_ids(sep)


# ---------------------------------------------------------------------------
# Hàm thuần (kiểm thử không cần torch)
# ---------------------------------------------------------------------------
def line_confidences(newlines: list[int], probs: list[float]) -> list[float]:
    """Độ tin cậy từng dòng = trung bình xác suất các token thuộc dòng đó.

    `newlines[i]` = số ký tự xuống dòng trong token i. Token chứa xuống dòng kết thúc
    dòng hiện tại và không tính vào dòng nào."""
    sums: list[float] = [0.0]
    counts: list[int] = [0]
    for nl, p in zip(newlines, probs, strict=False):
        if nl:
            for _ in range(nl):
                sums.append(0.0)
                counts.append(0)
            continue
        sums[-1] += p
        counts[-1] += 1
    return [round(s / c, 4) if c else 0.0 for s, c in zip(sums, counts, strict=True)]


def _clean_markdown(line: str) -> str | None:
    """Một dòng markdown -> chữ thuần. `None` = dòng trình bày (kẻ bảng, rào mã)."""
    s = line.strip()
    if not s or s.startswith("```"):
        return None
    if set(s) <= set("|-: "):
        return None                      # |---|---|
    s = s.lstrip("#").strip()
    s = s.replace("**", "").replace("__", "")
    if s.startswith("|") or s.endswith("|"):
        cells = [c.strip() for c in s.strip("|").split("|")]
        cells = [c for c in cells if c]
        if not cells:
            return None
        # Bảng hai ô (nhãn | giá trị) là dạng biểu mẫu: nối bằng ': ' để rule nhãn bắt được.
        s = f"{cells[0]}: {cells[1]}" if len(cells) == 2 else " | ".join(cells)
    return s or None


def to_lines(text: str, confs: list[float] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Văn bản mô hình trả về -> [{text, conf}] + cờ đã cắt vòng lặp."""
    raw = (text or "").split("\n")
    out: list[dict[str, Any]] = []
    looped = False
    run = 0
    for i, ln in enumerate(raw):
        s = _clean_markdown(ln)
        if s is None:
            continue
        if out and out[-1]["text"] == s:
            run += 1
            if run >= _MAX_REPEAT:
                looped = True
                continue
        else:
            run = 0
        conf = confs[i] if confs and i < len(confs) else 0.0
        out.append({"text": s, "conf": float(conf)})
    return out, looped


def join_halves(top: list[dict[str, Any]], bottom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ghép kết quả hai nửa ảnh chồng mép: bỏ các dòng đầu nửa dưới trùng dòng cuối nửa trên."""
    tail = {ln["text"] for ln in top[-3:]}
    k = 0
    while k < min(3, len(bottom)) and bottom[k]["text"] in tail:
        k += 1
    return top + bottom[k:]


# ---------------------------------------------------------------------------
# Đọc một ảnh
# ---------------------------------------------------------------------------
def _generate(image, max_tiles: int, max_new_tokens: int) -> tuple[str, list[float], bool]:
    """Một lượt sinh: (văn bản, conf từng dòng, có chạm trần token không)."""
    st = get_ocr()
    import torch  # noqa: PLC0415

    model, tok = st["model"], st["tokenizer"]
    pv = _pixel_values(image, max_tiles, torch, st["dtype"], st["device"])
    query, eos_id = _build_query(model, tok, OCR_PROMPT, pv.shape[0])
    model.img_context_token_id = tok.convert_tokens_to_ids("<IMG_CONTEXT>")
    enc = tok(query, return_tensors="pt")
    with torch.inference_mode():
        out = model.generate(
            pixel_values=pv,
            input_ids=enc["input_ids"].to(st["device"]),
            attention_mask=enc["attention_mask"].to(st["device"]),
            max_new_tokens=int(max_new_tokens), do_sample=False, num_beams=1,
            repetition_penalty=float(settings.vintern_repetition_penalty),
            eos_token_id=eos_id, return_dict_in_generate=True, output_scores=True,
        )
        seq = out.sequences[0]
        logp = model.language_model.compute_transition_scores(
            out.sequences, out.scores, normalize_logits=True)[0]
    ids = [int(x) for x in seq.tolist()]
    probs = [float(x) for x in logp.float().exp().tolist()]
    n = min(len(ids), len(probs))
    ids, probs = ids[-n:], probs[-n:]
    keep = [i for i, t in enumerate(ids) if t != eos_id]
    ids, probs = [ids[i] for i in keep], [probs[i] for i in keep]
    pieces = tok.convert_ids_to_tokens(ids)
    newlines = [(p or "").count(_BPE_NEWLINE) + (p or "").count("\n") for p in pieces]
    text = tok.decode(ids, skip_special_tokens=True)
    return text, line_confidences(newlines, probs), n >= int(max_new_tokens)


def transcribe(image, max_tiles: int | None = None, _depth: int = 0) -> list[dict[str, Any]]:
    """Ảnh PIL -> [{text, conf}] theo thứ tự đọc. Trang quá dày thì chia đôi rồi ghép."""
    tiles = int(max_tiles or settings.vintern_max_tiles)
    text, confs, truncated = _generate(image, tiles, settings.vintern_max_new_tokens)
    lines, looped = to_lines(text, confs)
    if looped:
        print("[ocr] Vintern lặp dòng — đã cắt phần lặp.")
    if truncated and _depth < 1:
        w, h = image.size
        overlap = int(h * 0.04)
        top = image.crop((0, 0, w, h // 2 + overlap))
        bottom = image.crop((0, h // 2 - overlap, w, h))
        return join_halves(transcribe(top, tiles, _depth + 1),
                           transcribe(bottom, tiles, _depth + 1))
    return lines


def quick_confidence(image) -> float:
    """Điểm 'đọc được' của một ảnh nhỏ (1 ô, vài chục token) — dùng chọn chiều xoay."""
    text, confs, _ = _generate(image, 1, 64)
    lines, _ = to_lines(text, confs)
    scored = [ln["conf"] for ln in lines if any(c.isalpha() for c in ln["text"])]
    return sum(scored) / len(scored) if scored else 0.0


# ---------------------------------------------------------------------------
# Chẩn đoán (npm run check) + nạp sẵn lúc khởi động
# ---------------------------------------------------------------------------
def describe_device(probe: bool = True) -> dict[str, Any]:
    """Môi trường OCR: bản torch/transformers, CUDA, model + revision; `probe` đọc thử ảnh."""
    info: dict[str, Any] = {
        "model": settings.vintern_model,
        "revision": settings.vintern_revision,
        "device_setting": settings.vintern_device,
        "max_tiles": settings.vintern_max_tiles,
    }
    try:
        import torch  # noqa: PLC0415
        import transformers  # noqa: PLC0415

        info.update(torch=torch.__version__, transformers=transformers.__version__,
                    cuda=bool(torch.cuda.is_available()))
        print(f"[ocr] Vintern · torch {info['torch']} · transformers {info['transformers']} · "
              f"CUDA {'có' if info['cuda'] else 'không'} · thiết bị {info['device_setting']}")
    except Exception as exc:  # noqa: BLE001
        info["error"] = repr(exc)
        print("[ocr] CẢNH BÁO: thiếu torch/transformers -> OCR không chạy. "
              "Cài lại: pip install -r requirements.txt")
        return info
    if probe:
        info["ocr_probe"] = probe_ocr()
        print("[ocr] THỬ ĐỌC ẢNH: OK" if info["ocr_probe"] == "ok"
              else f"[ocr] THỬ ĐỌC ẢNH: HỎNG -> {info['ocr_probe']}")
    return info


def probe_ocr() -> str:
    """Nạp model + đọc thử một ảnh trắng nhỏ. 'ok' hoặc mô tả lỗi."""
    try:
        from PIL import Image  # noqa: PLC0415

        _generate(Image.new("RGB", (448, 448), "white"), 1, 4)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def warmup_ocr() -> None:
    """Nạp sẵn model trong thread nền lúc khởi động (tắt: vintern_warmup=false)."""
    if not settings.vintern_warmup:
        return
    try:
        get_ocr()
    except Exception as exc:  # noqa: BLE001 — warm-up lỗi không chặn khởi động
        print(f"[ocr] warm-up bỏ qua: {exc}")


__all__ = [
    "OCR_PROMPT", "OcrUnavailableError", "describe_device", "get_ocr", "join_halves",
    "line_confidences", "probe_ocr", "quick_confidence", "reset_ocr", "tile_image",
    "to_lines", "transcribe", "warmup_ocr",
]
