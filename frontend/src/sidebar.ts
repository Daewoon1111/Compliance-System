/**
 * TRẠNG THÁI THANH BÊN TRÁI — "pinned" (mở rộng cố định) / "collapsed" (chỉ biểu tượng; rê
 * chuột vào thì thanh nổi lên đủ chữ, không đẩy nội dung). Nhớ qua lần mở lại phần mềm.
 *
 * Trạng thái gắn lên `<html data-sidebar>` để CSS quyết định bố cục — cùng một chỗ cho mọi
 * trang (khu người dùng lẫn khu quản trị), và `index.html` đặt sẵn trước khi React dựng
 * cây nên không nháy bố cục lúc mở.
 */
import { useSyncExternalStore } from "react";

export type SidebarMode = "pinned" | "collapsed";

const KEY = "datn6:sidebar";
const subs = new Set<() => void>();

function read(): SidebarMode {
  try {
    return localStorage.getItem(KEY) === "collapsed" ? "collapsed" : "pinned";
  } catch {
    return "pinned";
  }
}

let mode: SidebarMode = read();
document.documentElement.dataset.sidebar = mode;

export function toggleSidebar(): void {
  mode = mode === "pinned" ? "collapsed" : "pinned";
  document.documentElement.dataset.sidebar = mode;
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* bị chặn lưu -> chỉ áp cho lần dùng này */
  }
  subs.forEach((f) => f());
}

export function useSidebar(): SidebarMode {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => {
        subs.delete(cb);
      };
    },
    () => mode,
    () => mode,
  );
}
