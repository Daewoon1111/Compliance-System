import { useEffect } from "react";
import { Spinner } from "./Layout";
import { IconWarning } from "./Icons";
import { BTN, BTN_PRIMARY } from "../ui";
import { useT } from "../i18n";

/** CỬA SỔ XÁC NHẬN nhỏ — mọi thao tác xóa đều đi qua đây (Esc / bấm ra ngoài = hủy). */
export default function ConfirmDialog({
  title, message, confirmLabel, busy = false, onConfirm, onCancel,
}: {
  title: string; message: string; confirmLabel?: string; busy?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const t = useT();
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [busy, onCancel]);
  return (
    <div className="modal-backdrop !z-[70]" role="alertdialog" aria-modal="true" aria-label={title}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}>
      <div className="rise-in w-[min(440px,92vw)] rounded-2xl border border-slate-300 bg-surface p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-red-50 text-red-600">
            <IconWarning className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <div className="text-[16px] font-extrabold text-slate-800">{title}</div>
            <p className="m-0 mt-1 text-[13.5px] leading-relaxed text-slate-600">{message}</p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className={BTN} onClick={onCancel} disabled={busy} autoFocus>{t("common.cancel")}</button>
          <button type="button" className={BTN_PRIMARY + " !bg-red-600 hover:!bg-red-700"} onClick={onConfirm} disabled={busy}>
            {busy ? <><Spinner /> {t("set.deleting")}</> : confirmLabel || t("common.delete")}
          </button>
        </div>
      </div>
    </div>
  );
}
