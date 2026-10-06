import { useSyncExternalStore } from "react";

export type NoteKind = "error" | "warn" | "info" | "success";

export type Note = {
  id: number;
  kind: NoteKind;
  text: string;
  ts: number;
  /** Đường dẫn để BẤM VÀO thông báo là đi thẳng tới trang vừa chạy xong.
   *  CHỈ đặt cho thông báo HOÀN TẤT (kind "success"): cảnh báo hay thông báo
   *  "bắt đầu chạy" không dẫn đi đâu cả — bấm vào không được chuyển trang. */
  href?: string;
};

const MAX_HISTORY = 100;
let notes: Note[] = [];
let seq = 1;
const subs = new Set<() => void>();

function emit() {
  for (const s of subs) s();
}

function subscribe(cb: () => void) {
  subs.add(cb);
  return () => {
    subs.delete(cb);
  };
}

function getSnapshot() {
  return notes;
}

/** Hook đọc danh sách thông báo (tự re-render khi có thông báo mới). */
export function useNotes(): Note[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Đẩy 1 thông báo mới (mới nhất nằm đầu).
 *  `href` chỉ có tác dụng với thông báo hoàn tất (success). */
export function notify(kind: NoteKind, text: string, href?: string) {
  if (!text) return;
  const note: Note = { id: seq++, kind, text, ts: Date.now() };
  if (href && kind === "success") note.href = href;
  notes = [note, ...notes].slice(0, MAX_HISTORY);
  emit();
}

/** Xóa toàn bộ thông báo (nút "Xóa hết" trong bảng thông báo ở header). */
export function clearNotes() {
  notes = [];
  emit();
}
