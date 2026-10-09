import { API_BASE } from "./api/client";

// NHỊP SỐNG CỦA CỬA SỔ ỨNG DỤNG (bản desktop / USB).
//
// Launcher (desktop/launcher.py) mở giao diện trong một cửa sổ trình duyệt dạng ứng
// dụng và không có cách nào khác để biết cửa sổ đã đóng: Edge có thể vẫn chạy nền sau
// khi đóng cửa sổ. Trang báo "còn sống" định kỳ và báo "đóng" lúc bị gỡ khỏi màn hình;
// không còn tín hiệu thì launcher tắt backend + Ollama để không treo RAM của máy.
//
// Chỉ chạy ở bản build: bản web `npm run dev` do người dùng tự tắt bằng Ctrl+C.

const PING_MS = 15_000;

export function startDesktopHeartbeat(): void {
  if (!import.meta.env.PROD) return;
  const ping = () => {
    // Lỗi mạng ở đây không phải việc của người dùng: backend tắt thì các lệnh gọi API
    // thật sẽ báo lỗi bằng thông điệp riêng.
    fetch(`${API_BASE}/api/v1/desktop/ping`, { method: "POST", keepalive: true }).catch(() => {});
  };
  ping();
  setInterval(ping, PING_MS);
  // Cửa sổ thu nhỏ lâu thì trình duyệt giãn hẹn giờ; mở lại là báo ngay.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ping();
  });
  window.addEventListener("pagehide", () => {
    navigator.sendBeacon?.(`${API_BASE}/api/v1/desktop/bye`);
  });
}
