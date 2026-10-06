/**
 * CHẾ ĐỘ SÁNG / TỐI — kho nhỏ sống ngoài React (giống progressStore) để mọi nơi
 * đọc chung một nguồn sự thật và không phải bọc Context quanh toàn bộ ứng dụng.
 *
 * Cách tô màu: đặt/gỡ class `dark` trên <html>. Bảng màu tối được khai một lần
 * trong index.css (ánh xạ lại các lớp Tailwind đang dùng) nên không phải thêm
 * biến thể `dark:` vào từng dòng JSX của mọi trang.
 */
import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

const KEY = "datn6:theme";
const subs = new Set<() => void>();

function read(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

let theme: Theme = read();

function paint(t: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", t === "dark");
  root.style.colorScheme = t;
}

paint(theme);

export function getTheme(): Theme {
  return theme;
}

export function setTheme(t: Theme): void {
  if (t === theme) return;
  theme = t;
  paint(t);
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* localStorage bị chặn -> chỉ mất phần ghi nhớ, giao diện vẫn đổi */
  }
  subs.forEach((f) => f());
}

export function toggleTheme(): void {
  setTheme(theme === "dark" ? "light" : "dark");
}

function subscribe(cb: () => void) {
  subs.add(cb);
  return () => {
    subs.delete(cb);
  };
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme, getTheme);
}
