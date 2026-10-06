import { useEffect, useRef, useState } from "react";
import { useNotes, type Note, type NoteKind } from "../notify";
import { IconWarning, IconInfo, IconCheck } from "./Icons";

const BORDER: Record<NoteKind, string> = {
  error: "border-l-red-500",
  warn: "border-l-amber-500",
  info: "border-l-blue-500",
  success: "border-l-green-500",
};
const ICON: Record<NoteKind, (p: { className?: string }) => React.ReactNode> = {
  error: IconWarning,
  warn: IconWarning,
  info: IconInfo,
  success: IconCheck,
};
const ICON_TONE: Record<NoteKind, string> = {
  error: "text-red-600",
  warn: "text-amber-600",
  info: "text-blue-600",
  success: "text-green-600",
};

const TTL = 10000; // mỗi toast hiển thị ~10 giây rồi tự ẩn
const MAX = 4;

export function Toaster() {
  const notes = useNotes();
  const [visibleIds, setVisibleIds] = useState<number[]>([]);
  const seen = useRef<Set<number>>(new Set());

  // Phát hiện thông báo mới -> thêm vào toast + hẹn giờ tự gỡ (không gọi Date.now lúc render).
  useEffect(() => {
    for (const n of notes) {
      if (seen.current.has(n.id)) continue;
      seen.current.add(n.id);
      setVisibleIds((ids) => [n.id, ...ids]);
      setTimeout(() => setVisibleIds((ids) => ids.filter((x) => x !== n.id)), TTL);
    }
  }, [notes]);

  const byId = new Map(notes.map((n) => [n.id, n]));
  const toasts = visibleIds
    .map((id) => byId.get(id))
    .filter((n): n is Note => Boolean(n))
    .slice(0, MAX);

  if (!toasts.length) return null;

  return (
    <div className="fixed right-4 top-4 z-50 flex w-80 max-w-[90vw] flex-col gap-2">
      {toasts.map((n) => (
        <div
          key={n.id}
          className={
            "flex items-start gap-2 rounded-lg border border-slate-200 border-l-4 bg-white p-3 shadow-lg " +
            BORDER[n.kind]
          }
        >
          {(() => { const I = ICON[n.kind]; return <I className={"h-5 w-5 shrink-0 " + ICON_TONE[n.kind]} />; })()}
          <div className="text-[13px] leading-snug text-slate-800">{n.text}</div>
        </div>
      ))}
    </div>
  );
}
