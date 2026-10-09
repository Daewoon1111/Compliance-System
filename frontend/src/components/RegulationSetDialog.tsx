import { useEffect, useState } from "react";
import { REG_UPLOAD_MAX_MB, getRegulationSets } from "../api/client";
import type { RegulationSet } from "../types";
import { clearRegsetResult, startRegsetJob, useRegsetJob } from "../progressStore";
import { notify } from "../notify";
import { Spinner } from "./Layout";
import ProgressBar from "./ProgressBar";
import { IconCheck, IconEdit, IconMinus, IconScale, IconUpload, IconWarning, IconX } from "./Icons";
import { BTN, BTN_GHOST, BTN_PRIMARY, FIELD } from "../ui";
import { useT } from "../i18n";

const ACCEPT = [".md", ".txt", ".docx", ".pdf", ".zip"];

function sizeText(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toUpperCase() : "";
}

/** Màu nhãn đuôi tệp — chỉ để nhận ra loại tệp nhanh trong danh sách. */
const EXT_TONE: Record<string, string> = {
  PDF: "bg-red-100 text-red-700",
  DOCX: "bg-blue-100 text-blue-700",
  ZIP: "bg-amber-100 text-amber-700",
  MD: "bg-green-100 text-green-700",
  TXT: "bg-slate-100 text-slate-600",
};

/**
 * HỘP THOẠI "THÊM BỘ QUY ĐỊNH".
 *
 * Người dùng đặt tên cho bộ quy định rồi tải các văn bản lên — Markdown, văn bản thuần,
 * Word, PDF (có chữ hoặc bản chụp) hoặc một tệp .zip chứa các tệp đó. Hệ thống tự giải
 * nén, chuyển đổi và nạp vào kho quy định; bộ kiểm tra chọn bộ quy định nào thì chỉ đối
 * chiếu với văn bản của bộ đó. Đặt trùng tên bộ đã có = thêm văn bản vào bộ đó.
 *
 * Việc nạp chạy ở KHO TIẾN TRÌNH (`progressStore`), không ở hộp thoại: đóng hộp thoại
 * giữa chừng thì việc vẫn chạy ngầm, tiến độ hiện ở bảng Thông báo, mở lại thì thấy tiếp.
 * Đang nạp thì KHÔNG sửa được danh sách tệp (không có nút xóa / thay đổi).
 */
export default function RegulationSetDialog({
  onClose,
  onAdded,
  initialName = "",
}: {
  onClose: () => void;
  /** Gọi sau khi nạp xong (tên bộ quy định vừa nạp) — nơi mở hộp thoại tự làm mới. */
  onAdded?: (setName: string) => void;
  initialName?: string;
}) {
  const t = useT();
  const job = useRegsetJob();
  const busy = job.active;
  const [name, setName] = useState(busy ? job.name : initialName);
  const [files, setFiles] = useState<File[]>([]);
  // Đã chọn tệp -> vùng kéo thả thu lại thành nút "Thay đổi"; bấm mới hiện lại cùng nút xóa.
  const [editing, setEditing] = useState(false);
  const [ocr, setOcr] = useState(false);
  const [error, setError] = useState("");
  const [sets, setSets] = useState<RegulationSet[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const result = job.result;
  const shownError = error || job.error;

  useEffect(() => {
    getRegulationSets().then((d) => setSets(d.regulation_sets || [])).catch(() => {});
  }, [result]);

  /** Đóng: đang nạp thì việc chạy tiếp ở nền — nói rõ để người dùng không tưởng đã hủy. */
  function close() {
    if (busy) notify("info", t("rset.bgToast").replace("{name}", job.name));
    else clearRegsetResult();
    onClose();
  }

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  });

  function add(list: FileList | null) {
    if (!list || busy) return;
    const ok = Array.from(list).filter((f) => ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext)));
    setError(ok.length < list.length ? t("rset.errType") : "");
    if (job.result || job.error) clearRegsetResult();
    setFiles((prev) => {
      const map = new Map(prev.map((f) => [f.name + f.size, f]));
      ok.forEach((f) => map.set(f.name + f.size, f));
      return Array.from(map.values());
    });
    if (ok.length) setEditing(false);
  }

  const totalBytes = files.reduce((n, f) => n + f.size, 0);
  const tooBig = totalBytes > REG_UPLOAD_MAX_MB * 1048576;

  function submit() {
    if (!name.trim()) return setError(t("rset.errName"));
    if (!files.length) return setError(t("rset.errFiles"));
    // Chặn ngay ở trình duyệt: gửi 300 MB lên rồi mới nhận lỗi là bắt người dùng chờ vô ích.
    if (tooBig) return setError(t("rset.errSize").replace("{max}", String(REG_UPLOAD_MAX_MB)));
    setError("");
    setEditing(false);
    startRegsetJob(name.trim(), files, ocr, (setName) => {
      setFiles([]);
      onAdded?.(setName);
    });
  }

  const existing = sets.find((s) => s.name.toLowerCase() === name.trim().toLowerCase());
  const showDrop = !busy && (files.length === 0 || editing);
  const canRemove = !busy && editing;
  // Đang nạp: danh sách là tệp của việc đang chạy (mở lại hộp thoại vẫn thấy đúng tệp).
  const rows: { key: string; name: string; size: number | null }[] = busy
    ? job.files.map((n) => ({ key: n, name: n, size: null }))
    : files.map((f) => ({ key: f.name + f.size, name: f.name, size: f.size }));

  return (
    <div className="modal-backdrop !z-[60]" role="dialog" aria-modal="true" aria-label={t("rset.title")}>
      <div className="rise-in flex max-h-[90vh] w-[min(760px,94vw)] flex-col overflow-hidden rounded-[18px] border border-slate-300 bg-[var(--c-bg)] shadow-2xl">
        <div className="flex items-center gap-4 border-b border-slate-200 px-6 py-4">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-blue-200 bg-blue-50 text-blue-700">
            <IconScale className="h-6 w-6" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[18px] font-extrabold text-slate-800">{t("rset.title")}</div>
            <div className="text-[13px] text-slate-500">{t("rset.subtitle")}</div>
          </div>
          {busy ? (
            // THU NHỎ: đang nạp thì việc chạy tiếp ở nền, tiến độ ở thanh dưới cùng.
            <button
              type="button"
              onClick={close}
              className="grid h-9 w-9 cursor-pointer place-items-center rounded-lg border-0 bg-transparent text-slate-400 hover:bg-slate-100 hover:text-slate-800"
              title={t("rset.minimize")}
              aria-label={t("rset.minimize")}
            >
              <IconMinus className="h-5 w-5" />
            </button>
          ) : null}
          <button
            type="button"
            onClick={close}
            className="grid h-9 w-9 cursor-pointer place-items-center rounded-lg border-0 bg-transparent text-slate-400 hover:bg-slate-100 hover:text-slate-800"
            title={busy ? t("rset.closeBg") : t("common.close")}
            aria-label={busy ? t("rset.closeBg") : t("common.close")}
          >
            <IconX className="h-5 w-5" />
          </button>
        </div>

        <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-6">
          {shownError ? (
            <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
              <IconWarning className="h-5 w-5 shrink-0" /> {shownError}
            </div>
          ) : null}
          {result && !busy ? (
            <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
              <div className="flex items-center gap-2 font-bold"><IconCheck className="h-5 w-5" /> {result.note}</div>
              <ul className="m-0 mt-1.5 list-disc pl-6 text-[12.5px]">
                {result.added.map((a) => <li key={a.saved_as}>{a.file} ({a.note})</li>)}
              </ul>
              {result.skipped.length ? (
                <ul className="m-0 mt-1.5 list-disc pl-6 text-[12.5px] text-red-700">
                  {result.skipped.map((s) => <li key={s.file}>{s.file}: {s.error}</li>)}
                </ul>
              ) : null}
            </div>
          ) : null}

          {busy ? (
            <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3.5">
              <div className="mb-2 flex items-center gap-2 text-[13.5px] font-bold text-blue-800">
                <Spinner dark /> {t("rset.runningName").replace("{name}", job.name)}
                {job.pct !== null ? <span className="ml-auto font-mono text-[12px]">{job.pct}%</span> : null}
              </div>
              <ProgressBar pct={job.pct} label={t("rset.running")} />
              <div className="mt-2 text-[12.5px] text-slate-600">{job.text}{job.eta ? ` · ${job.eta}` : ""}</div>
              <div className="mt-1 text-[12px] text-slate-500">{t("rset.bgHint")}</div>
            </div>
          ) : null}

          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-bold text-slate-600">
              {t("rset.name")} <b className="text-red-600">*</b>
            </span>
            <input className={FIELD + " h-11"} value={busy ? job.name : name} list="reg-set-names" onChange={(e) => setName(e.target.value)} placeholder={t("rset.namePh")} disabled={busy} autoFocus={!busy} />
            <datalist id="reg-set-names">{sets.map((s) => <option key={s.name} value={s.name} />)}</datalist>
            {existing && !busy ? (
              <span className="mt-1 block text-[12px] text-blue-700">
                {t("rset.appendNote").replace("{n}", String(existing.documents))}
              </span>
            ) : null}
          </label>

          {showDrop ? (
            <label
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); add(e.dataTransfer.files); }}
              className={"drop-zone flex cursor-pointer flex-col items-center justify-center gap-1.5 px-4 py-7 text-center" + (dragOver ? " is-over" : "")}
            >
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-blue-50 text-blue-700">
                <IconUpload className="h-6 w-6" />
              </span>
              <span className="text-[14.5px] font-bold text-slate-800">{t("rset.drop")}</span>
              <span className="text-[12.5px] text-slate-500">{t("rset.dropHint")}</span>
              <input type="file" multiple accept={ACCEPT.join(",")} className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
            </label>
          ) : null}

          {rows.length ? (
            <div className="rounded-xl border border-slate-200">
              <div className="flex items-center gap-2 border-b border-slate-200 px-3.5 py-2">
                <span className="flex-1 text-[13px] font-bold text-slate-700">{t("rset.chosen")} ({rows.length})</span>
                {!busy && !editing ? (
                  <button type="button" className={BTN + " !px-3 !py-1 text-[12.5px]"} onClick={() => setEditing(true)}>
                    <IconEdit className="h-4 w-4" /> {t("rset.change")}
                  </button>
                ) : null}
                {!busy && editing && files.length ? (
                  <button type="button" className={BTN + " !px-3 !py-1 text-[12.5px]"} onClick={() => setEditing(false)}>
                    <IconCheck className="h-4 w-4" /> {t("rset.changeDone")}
                  </button>
                ) : null}
              </div>
              <ul className="m-0 grid list-none gap-1 p-1.5">
                {rows.map((f, i) => (
                  <li key={f.key} className="flex items-center gap-3 rounded-lg px-2.5 py-1.5 hover:bg-slate-50">
                    <span className={"w-11 shrink-0 rounded-md py-1 text-center text-[10px] font-extrabold " + (EXT_TONE[extOf(f.name)] ?? EXT_TONE.TXT)}>
                      {extOf(f.name)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-700">{f.name}</span>
                    {f.size !== null ? <span className="font-mono text-[11.5px] text-slate-400">{sizeText(f.size)}</span> : null}
                    {canRemove ? (
                      <button type="button" onClick={() => setFiles((p) => p.filter((_, j) => j !== i))}
                        className="grid h-7 w-7 cursor-pointer place-items-center rounded-md border-0 bg-transparent text-slate-400 hover:bg-red-50 hover:text-red-600"
                        aria-label={`${t("up.removeFile")}: ${f.name}`} title={t("up.removeFile")}>
                        <IconX className="h-4 w-4" />
                      </button>
                    ) : null}
                  </li>
                ))}
                {!busy ? (
                  <li className={"flex justify-end px-2.5 pb-0.5 pt-1 font-mono text-[11.5px] " + (tooBig ? "font-bold text-red-600" : "text-slate-500")}>
                    {t("rset.total")}: {sizeText(totalBytes)} / {REG_UPLOAD_MAX_MB} MB
                  </li>
                ) : null}
              </ul>
            </div>
          ) : null}

          {!busy ? (
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-surface px-4 py-3">
              <span className="min-w-0 flex-1">
                <span className="block text-[13.5px] font-bold text-slate-800">{t("rset.ocr")}</span>
                <span className="block text-[12.5px] text-slate-500">{t("rset.ocrHint")}</span>
              </span>
              <span className="switch mt-0.5">
                <input type="checkbox" role="switch" checked={ocr} onChange={(e) => setOcr(e.target.checked)} aria-label={t("rset.ocr")} />
                <i />
              </span>
            </label>
          ) : null}
        </div>

        <div className="flex items-center gap-3 border-t border-slate-200 px-6 py-3.5">
          <span className="flex flex-1 items-center text-[12.5px] text-slate-500">
            {busy ? t("rset.bgHintShort") : null}
          </span>
          {busy ? null : <button type="button" className={BTN_GHOST} onClick={close}>{t("common.close")}</button>}
          <button type="button" className={BTN_PRIMARY} disabled={busy || !files.length || !name.trim() || tooBig} onClick={submit}>
            {busy ? <><Spinner /> {t("rset.submitting")}</> : t("rset.submit")}
          </button>
        </div>
      </div>
    </div>
  );
}
