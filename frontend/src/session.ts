// Ghi nhớ BƯỚC hiện tại của luồng "Kiểm tra" (tải lên / review / kết quả) để khi người
// dùng sang Thống kê/Lịch sử/Quản trị rồi bấm lại "Kiểm tra" thì QUAY VỀ ĐÚNG bước đó
// (không nhảy về trang tải lên, không mất phiên). Dùng localStorage -> bền qua reload.
const KEY = "datn6:lastCheckPath";

export function setLastCheckPath(path: string): void {
  try {
    localStorage.setItem(KEY, path);
  } catch {
    /* localStorage bị chặn -> bỏ qua, chỉ mất tính năng ghi nhớ */
  }
}

export function getLastCheckPath(): string {
  try {
    return localStorage.getItem(KEY) || "/kiem-tra";
  } catch {
    return "/";
  }
}
