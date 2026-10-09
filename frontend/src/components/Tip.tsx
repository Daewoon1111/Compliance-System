import { useSyncExternalStore, type ReactNode } from "react";
import { IconX } from "./Icons";
import { useT } from "../i18n";

// Mẹo đã tắt — nhớ ở BỘ NHỚ của trang, CỐ Ý không ghi localStorage: tắt thì gọn màn hình
// suốt lần dùng này, mở lại phần mềm thì mẹo hiện lại cho người cần nhắc.
const hidden = new Set<string>();
const subs = new Set<() => void>();
let version = 0;

function subscribe(cb: () => void) {
  subs.add(cb);
  return () => {
    subs.delete(cb);
  };
}

function hideTip(id: string) {
  hidden.add(id);
  version += 1;
  subs.forEach((f) => f());
}

function useTipHidden(id: string): boolean {
  useSyncExternalStore(subscribe, () => version, () => version);
  return hidden.has(id);
}

/** KHỐI MẸO / HƯỚNG DẪN tắt được: nút X ở góc phải, tắt thì biến mất tới lần mở phần mềm sau. */
export default function Tip({ id, className = "", children }: { id: string; className?: string; children: ReactNode }) {
  const t = useT();
  if (useTipHidden(id)) return null;
  return (
    <div className={"relative pr-8 " + className}>
      {children}
      <button
        type="button"
        onClick={() => hideTip(id)}
        className="absolute right-1.5 top-1.5 grid h-6 w-6 cursor-pointer place-items-center rounded-md border-0 bg-transparent text-slate-400 hover:bg-slate-200/70 hover:text-slate-700"
        title={t("tip.hide")}
        aria-label={t("tip.hide")}
      >
        <IconX className="h-4 w-4" />
      </button>
    </div>
  );
}
