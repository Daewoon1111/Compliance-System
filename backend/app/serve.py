"""KHỞI CHẠY (serve) — kiểm tra môi trường, chốt cổng :8000 rồi mở uvicorn.

Ba chế độ, dùng chung một đường mã nên `npm run check` và phần kiểm tra chạy trước
server không thể lệch nhau:

  python -m app.serve            chốt cổng + kiểm tra + mở server   (npm run dev/admin)
  python -m app.serve --check    kiểm tra môi trường + chỉ số + đo hồ sơ có nhãn (npm run check)
  python -m app.serve --smoke    như --check, thêm: dựng backend THẬT, gọi /health, tắt (npm run smoke)
  python -m app.serve --stop     giải phóng cổng 5173/5174/8000     (npm run stop)

Cổng :8000 đã có tiến trình NGHE nghĩa là một cửa sổ dev/admin khác đã mở backend.
Khi đó KHÔNG mở thêm tiến trình thứ hai — bind trùng ném `[Errno 10048]`, và
`--kill-others-on-fail` của concurrently sẽ giết luôn frontend của cửa sổ này. Thay
vào đó báo dùng chung rồi thoát mã 0 để phần frontend chạy tiếp.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import sys
import time

from app.core import force_utf8_streams, setup_runtime

HOST = "127.0.0.1"
PORT = 8000
# Cổng của cả hệ: 5173 giao diện người dùng · 5174 giao diện quản trị · 8000 backend.
ALL_PORTS = (5173, 5174, PORT)


def port_busy(host: str = HOST, port: int = PORT) -> bool:
    """Cổng đã có tiến trình NGHE chưa.

    Thử KẾT NỐI chứ không thử bind: bind-để-thử tự nó chiếm cổng trong khoảnh khắc
    giữa lúc thử và lúc uvicorn bind thật, tạo ra đúng cuộc đua mình đang muốn tránh."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _report_state() -> None:
    """Số liệu VẬN HÀNH đọc từ nhật ký: chỉ số kỹ thuật gộp + phép đo trên hồ sơ có nhãn.

    Cả hai trước đây chỉ xem được bằng cách mở trang quản trị hoặc gõ `python -m
    app.eval` — nên trên thực tế không ai chạy. Cả hai đều đọc artefact đã lưu (không
    gọi mô hình, không OCR lại) nên gắn vào `--check` không làm lệnh này chậm đi đáng
    kể, và mỗi lần kiểm tra môi trường là một lần nhìn thấy hệ đang ở đâu.

    Hỏng ở đây KHÔNG được làm hỏng lệnh kiểm tra: nhật ký rỗng, kho luật chưa nạp hay
    thư mục nhãn chưa có đều là trạng thái hợp lệ của một máy vừa cài."""
    try:
        from app.store import technical_metrics  # noqa: PLC0415

        t = technical_metrics(days=30)["total"]
        print(f"[chỉ số] 30 ngày: {t['runs']} lượt · {t['files']} file · {t['pages']} trang · "
              f"OCR {t['ocr_seconds']:.0f}s · kiểm tra {t['check_seconds']:.0f}s")
    except Exception as exc:  # noqa: BLE001
        print(f"[chỉ số] không đọc được nhật ký: {exc!r}")
    try:
        from app.eval import print_report, run_eval  # noqa: PLC0415

        print_report(run_eval())
    except Exception as exc:  # noqa: BLE001
        print(f"[eval] không chạy được phép đo: {exc!r}")


def run_checks(state: bool = False) -> int:
    """Preflight LLM (chế độ mềm) + thiết bị OCR, kèm chỉ số vận hành khi `state=True`.

    `state` chỉ bật ở `--check` và `--smoke`: hai lệnh chẩn đoán, người gõ đang chờ đọc
    kết quả. Đường mở server thì KHÔNG — nó phải lên nhanh nhất có thể, và băm lại kho
    luật để in một dòng thống kê là cái giá trả mỗi lần `npm run dev`.

    Các lệnh import nằm TRONG hàm: lệnh `--stop` không phải trả giá nạp torch/transformers."""
    setup_runtime()
    from app.core import production_config_problems  # noqa: PLC0415
    from app.domain.documents.ocr import describe_device  # noqa: PLC0415
    from app.llm import preflight  # noqa: PLC0415

    code = asyncio.run(preflight(True))
    # `describe_device` TỰ in phần người đọc cần (bản torch/transformers, CUDA, kết quả
    # thử đọc ảnh). Chỉ thử đọc ảnh ở lệnh chẩn đoán: nạp model mất vài chục giây.
    describe_device(probe=state)
    # LIỆT KÊ chứ không chặn: ở máy cá nhân (`app_env=local`) cấu hình mở là ĐÚNG.
    # Chặn khởi động chỉ xảy ra khi `app_env=production` — xem `check_production_config`.
    for p in production_config_problems():
        print(f"[cấu hình] {p}")
    if state:
        _report_state()
    return code


def smoke(host: str = HOST, port: int = 0) -> int:
    """Dựng backend THẬT trong tiến trình này, gọi `/health`, rồi tắt. 0 = đạt.

    Khác `--check` ở chỗ nó đi qua ĐÚNG đường khởi động thật: import `app.main`
    (kéo theo `setup_runtime` + toàn bộ router), chạy lifespan, mở socket. Đây là
    thứ bắt được lỗi DLL/native và lỗi thứ tự import — những lỗi mà unit test không
    thấy vì test cố tình chặn đường nạp model.

    `port=0` để hệ điều hành tự cấp cổng trống: chạy smoke không được đụng vào cổng
    :8000 của phiên dev đang mở."""
    server = thread = None
    try:
        import threading  # noqa: PLC0415

        import httpx  # noqa: PLC0415
        import uvicorn  # noqa: PLC0415

        setup_runtime()
        from app.main import app  # noqa: PLC0415 — sau setup_runtime

        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(600):                     # tối đa 60s cho lifespan + nạp OCR
            if server.started and server.servers:
                break
            time.sleep(0.1)
        else:
            print("[smoke] KHÔNG dựng được server trong 60 giây")
            return 1
        bound = server.servers[0].sockets[0].getsockname()[1]
        # `trust_env=False`: gọi vào chính mình qua loopback thì KHÔNG được đi qua
        # proxy của hệ thống. Máy có HTTP_PROXY/ALL_PROXY (VPN công ty) mà không tắt
        # thì smoke báo lỗi mạng trong khi backend hoàn toàn khoẻ.
        with httpx.Client(trust_env=False, timeout=30) as c:
            r = c.get(f"http://{host}:{bound}/health")   # KHÔNG có tiền tố /api/v1
        ok = r.status_code == 200
        print(f"[smoke] /health -> {r.status_code} {r.text[:120]}")
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 — smoke phải BÁO lỗi, không được ném lên
        # Lệnh chẩn đoán mà đổ traceback thì mất hết tác dụng: nó tồn tại để nói
        # NGUYÊN NHÂN, và lỗi thiếu thư viện/DLL chính là thứ nó phải bắt được.
        print(f"[smoke] THẤT BẠI: {exc!r}")
        return 1
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)


def _pids_on_ports(ports: tuple[int, ...]) -> set[int]:
    """PID đang NGHE trên các cổng đã cho, đọc từ `netstat -ano` (Windows)."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    wanted = {f":{p}" for p in ports}
    pids: set[int] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[-1].isdigit() \
                and any(parts[1].endswith(w) for w in wanted):
            pids.add(int(parts[-1]))
    return pids


def stop_ports(ports: tuple[int, ...] = ALL_PORTS) -> int:
    """Kết liễu tiến trình đang giữ các cổng của hệ (tiến trình mồ côi sau khi crash)."""
    pids = _pids_on_ports(ports)
    if not pids:
        print(f"[serve] cổng {'/'.join(map(str, ports))} đang trống")
        return 0
    for pid in sorted(pids):
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, check=False)
        print(f"[serve] dừng PID {pid}" if r.returncode == 0
              else f"[serve] KHÔNG dừng được PID {pid}: {r.stderr.strip()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    force_utf8_streams()
    ap = argparse.ArgumentParser(prog="app.serve", description="Khởi chạy backend DATN_6.")
    ap.add_argument("--check", action="store_true", help="chỉ kiểm tra môi trường, không mở server")
    ap.add_argument("--smoke", action="store_true", help="dựng backend thật, gọi /health, rồi tắt")
    ap.add_argument("--stop", action="store_true", help="giải phóng cổng 5173/5174/8000")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args(argv)

    if args.stop:
        return stop_ports()
    if args.smoke:
        return run_checks(state=True) or smoke(args.host)
    if args.check:
        return run_checks(state=True)
    if port_busy(args.host, args.port):
        print(f"[serve] backend :{args.port} đang chạy - dùng chung, không mở thêm tiến trình")
        return 0
    run_checks()
    from app.core import ADMIN_TOKEN_FILE, ensure_admin_token  # noqa: PLC0415

    print(f"[admin] mã quản trị (trang Quản trị): {ensure_admin_token()}  ({ADMIN_TOKEN_FILE.name})")
    import uvicorn  # noqa: PLC0415 — nặng, chỉ nạp khi thật sự mở server

    uvicorn.run("app.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
