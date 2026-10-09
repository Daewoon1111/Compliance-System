import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  friendly, getActiveFieldSet, getFieldSets, getRegulationSets, setActiveFieldSet,
} from "../api/client";
import {
  clearUploadResult,
  getUploadJob,
  startUploadJob,
  subscribeUploadJob,
} from "../progressStore";
import type { FieldSetInfo, FileRegion, RegulationSet } from "../types";
import { AppShell, Alert, PageHeader, Spinner } from "../components/Layout";
import { notify } from "../notify";
import { setLastCheckPath } from "../session";
import { BTN, BTN_GHOST, BTN_PRIMARY, FIELD, fileKey } from "../ui";
import {
  IconArrowRight, IconCheck, IconCrop, IconEdit, IconFile, IconInfo, IconPlus, IconScale, IconSliders,
  IconStack, IconUpload, IconX,
} from "../components/Icons";
import { translate, useT } from "../i18n";
import RegionPicker from "../components/RegionPicker";
import CheckSetDialog from "../components/CheckSetDialog";
import RegulationSetDialog from "../components/RegulationSetDialog";

// HẠN MỨC — phải khớp MAX_FILES / MAX_FILE_BYTES / MAX_TOTAL_BYTES ở
// backend/app/routers/sessions.py. Chặn tại đây để người dùng biết ngay thay vì
// chờ upload xong mới nhận 413.
const MAX_FILES = 20;
const MAX_FILE_MB = 50;
const MAX_TOTAL_MB = 150;

// Lưu nháp ở cấp module -> điều hướng qua lại (vd "Trở lại" ở trang 2) vẫn giữ lựa chọn.
const draft = {
  files: [] as File[],
  regionsOn: false,
  regions: {} as Record<string, FileRegion>,
};

function fmtSize(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** MỤC của thẻ chính: nhãn bước + tiêu đề + mô tả, rồi nội dung. Số bước là thứ tự
 *  thật người dùng làm (chọn bộ kiểm tra -> tải tài liệu -> tinh chỉnh tùy chọn). */
function Section({
  eyebrow, icon, title, desc, aside, done, tour, children,
}: {
  eyebrow: string; icon: ReactNode; title: string; desc: string;
  aside?: ReactNode; done?: boolean; tour?: string; children: ReactNode;
}) {
  return (
    <section className="border-t border-slate-200 first:border-t-0" data-tour={tour}>
      <div className="flex items-start gap-4 px-6 pb-4 pt-5">
        <span className={"grid h-10 w-10 shrink-0 place-items-center rounded-[11px] border " +
          (done ? "border-green-200 bg-green-50 text-green-600" : "border-blue-200 bg-blue-50 text-blue-700")}>
          {done ? <IconCheck className="h-5 w-5" /> : icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="eyebrow">{eyebrow}</div>
          <h3 className="m-0 mt-1 text-[16.5px] font-extrabold text-slate-800">{title}</h3>
          <p className="m-0 mt-0.5 text-[13px] text-slate-500">{desc}</p>
        </div>
        {aside ? <div className="shrink-0">{aside}</div> : null}
      </div>
      <div className="px-6 pb-6">{children}</div>
    </section>
  );
}

/** Thẻ nhỏ ở CỘT PHẢI. */
function RailCard({ title, icon, aside, children }: { title: string; icon: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <div className="surface-card p-5">
      <div className="mb-3.5 flex items-center gap-2.5">
        <span className="text-blue-700">{icon}</span>
        <span className="flex-1 text-[14.5px] font-extrabold text-slate-800">{title}</span>
        {aside}
      </div>
      {children}
    </div>
  );
}

export default function Upload() {
  const [fieldSets, setFieldSets] = useState<FieldSetInfo[]>([]);
  const [regSets, setRegSets] = useState<RegulationSet[]>([]);
  // BỘ KIỂM TRA ĐANG DÙNG — nhớ ở backend, đổi ngay tại ô chọn hoặc tạo trong hộp thoại.
  const [fieldSetId, setFieldSetId] = useState("");
  const [switching, setSwitching] = useState(false);
  const [files, setFiles] = useState<File[]>(draft.files);
  // VÙNG CẦN KIỂM TRA: công tắc; bật -> mở cửa sổ khoanh vùng. Khóa theo `fileKey`.
  const [regionsOn, setRegionsOn] = useState(draft.regionsOn);
  const [regions, setRegions] = useState<Record<string, FileRegion>>(draft.regions);
  const [regionPickerOpen, setRegionPickerOpen] = useState(false);
  const [dialog, setDialog] = useState<"" | "checkset" | "regset">("");
  const [dragOver, setDragOver] = useState(false);
  // Đã chọn tệp -> vùng kéo thả thu lại thành nút "Thay đổi"; bấm mới hiện lại cùng nút xóa.
  const [editingFiles, setEditingFiles] = useState(false);
  // Tiến trình OCR nằm ở KHO TOÀN CỤC (progressStore) -> chuyển trang không mất;
  // quay lại trang này vẫn thấy tiến độ, xong việc tự chuyển sang bước kiểm tra.
  const job = useSyncExternalStore(subscribeUploadJob, getUploadJob);
  const loading = job.active;
  const [pageError, setPageError] = useState("");
  const error = pageError || job.error;
  // Đang đọc hồ sơ thì danh sách tệp KHÓA: không xóa, không thay đổi (đang gửi đúng các tệp này).
  const showDrop = !loading && (files.length === 0 || editingFiles);
  const canRemove = !loading && editingFiles;
  const nav = useNavigate();
  const t = useT();

  // Xong (kể cả khi xong lúc đang ở trang khác) -> chuyển sang bước kiểm tra.
  useEffect(() => {
    if (!job.sessionId) return;
    const sid = job.sessionId;
    clearUploadResult();
    nav(`/review/${sid}`);
  }, [job.sessionId, nav]);

  // Đang ở bước TẢI LÊN -> "Kiểm tra" trên thanh điều hướng quay về đây.
  useEffect(() => {
    setLastCheckPath("/kiem-tra");
  }, []);

  // Bỏ vùng của file đã bị gỡ khỏi danh sách; hết file thì tắt chế độ chọn vùng.
  const liveRegions = useMemo(() => {
    const keys = new Set(files.map(fileKey));
    return Object.fromEntries(Object.entries(regions).filter(([k]) => keys.has(k)));
  }, [files, regions]);
  const regionsActive = regionsOn && files.length > 0;

  useEffect(() => {
    Object.assign(draft, { files, regionsOn: regionsActive, regions: liveRegions });
  }, [files, regionsActive, liveRegions]);

  const loadFieldSets = useCallback(() => {
    Promise.all([getFieldSets(), getActiveFieldSet()])
      .then(([r, a]) => {
        setFieldSets(r.field_sets || []);
        setFieldSetId(a.field_set || "");
      })
      // `translate` (không phải hook `t`) để effect chạy-một-lần không phải khai `t`.
      .catch((e) => setPageError(translate("toast.fieldSetsFailed") + friendly(e)));
  }, []);

  const loadRegSets = useCallback(() => {
    getRegulationSets().then((d) => setRegSets(d.regulation_sets || [])).catch(() => {});
  }, []);

  useEffect(() => {
    loadFieldSets();
    loadRegSets();
  }, [loadFieldSets, loadRegSets]);

  const usable = useMemo(() => fieldSets.filter((s) => !s.error && s.fields > 0), [fieldSets]);
  const chosen = useMemo(
    () => fieldSets.find((f) => f.id === fieldSetId) || null,
    [fieldSets, fieldSetId],
  );

  /** Đổi bộ kiểm tra ngay tại ô chọn — backend nhớ lựa chọn cho lần mở sau. */
  function switchFieldSet(id: string) {
    if (!id || id === fieldSetId) return;
    const prev = fieldSetId;
    setFieldSetId(id);
    setSwitching(true);
    setActiveFieldSet(id)
      .catch((e) => {
        setFieldSetId(prev);
        notify("error", t("toast.fieldSetsFailed") + friendly(e));
      })
      .finally(() => setSwitching(false));
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    const all = Array.from(list);
    const pdfs = all.filter(
      (f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"),
    );
    if (pdfs.length < all.length) notify("warn", t("up.errNotPdf"));
    const tooBig = pdfs.filter((f) => f.size > MAX_FILE_MB * 1048576);
    if (tooBig.length)
      notify("error", `${t("up.errFileTooBig")} (${MAX_FILE_MB} MB): ${tooBig.map((f) => f.name).join(", ")}`);
    const incoming = pdfs.filter((f) => f.size <= MAX_FILE_MB * 1048576);
    setFiles((prev) => {
      const map = new Map(prev.map((f) => [fileKey(f), f]));
      for (const f of incoming) map.set(fileKey(f), f);
      const next = Array.from(map.values());
      if (next.length > MAX_FILES) {
        notify("error", `${t("up.errTooManyFiles")} (${MAX_FILES}).`);
        return next.slice(0, MAX_FILES);
      }
      return next;
    });
    if (incoming.length) setEditingFiles(false);
  }

  // Có bộ kiểm tra dùng được thì mới cho chọn file — bộ kiểm tra quyết định trích xuất gì.
  const choiceDone = !!chosen && !chosen.error && chosen.fields > 0;
  const canSubmit = choiceDone && files.length > 0;

  const regionSummary = useMemo(() => {
    let cropped = 0;
    let skipped = 0;
    Object.values(liveRegions).forEach((r) => {
      cropped += Object.keys(r.rects).length;
      skipped += r.skip.length;
    });
    return { cropped, skipped };
  }, [liveRegions]);

  function toggleRegions() {
    if (!files.length || loading) return;
    if (regionsOn) {
      setRegionsOn(false); // tắt = quét toàn bộ mọi trang như bình thường
      setRegions({});
      return;
    }
    setRegionsOn(true);
    setRegionPickerOpen(true);
  }

  async function onConfirm() {
    setPageError("");
    if (!choiceDone) return setPageError(t("up.errFieldSet"));
    if (files.length === 0) return setPageError(t("up.errFiles"));
    if (files.reduce((s, f) => s + f.size, 0) > MAX_TOTAL_MB * 1048576)
      return setPageError(`${t("up.errTotalTooBig")} (${MAX_TOTAL_MB} MB).`);
    notify("info", `${t("toast.ocrStart")} ${files.length} file…`);
    // Chạy qua KHO TOÀN CỤC: SSE + request sống ngoài component -> chuyển trang
    // không mất tiến trình; kết quả/lỗi được useEffect phía trên xử lý.
    startUploadJob(
      files,
      fieldSetId,
      regionsActive ? files.map((f) => liveRegions[fileKey(f)] ?? null) : null,
    );
  }

  const totalBytes = files.reduce((s, f) => s + f.size, 0);

  // Bộ quy định bộ kiểm tra đang đối chiếu (rỗng = toàn kho).
  const linkedRegs = chosen?.regulation_sets?.length
    ? chosen.regulation_sets.map((name) => regSets.find((r) => r.name === name) ?? { name, documents: 0, files: [], titles: [] })
    : [];
  const allDocs = regSets.reduce((s, r) => s + r.documents, 0);

  // Trạng thái thanh hành động: thiếu gì thì nói đúng cái đó.
  const readiness = !choiceDone
    ? { ok: false, title: t("up.needSet"), desc: t("up.needSetDesc") }
    : !files.length
      ? { ok: false, title: t("up.needFiles"), desc: t("up.needFilesDesc") }
      : { ok: true, title: t("up.ready"), desc: `${files.length} ${t("up.fileUnit")} · ${fmtSize(totalBytes)} · ${chosen?.display_name}` };

  return (
    <AppShell step={1}>
      <PageHeader
        title={t("up.title")}
        desc={t("up.lead")}
        actions={
          <button type="button" className={BTN_GHOST} disabled={loading} onClick={() => setDialog("regset")}>
            <IconPlus className="h-4.5 w-4.5" /> {t("up.addRegSet")}
          </button>
        }
      />

      {error ? <Alert kind="error">{error}</Alert> : null}

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <div className="surface-card overflow-hidden">
            {/* 01 — BỘ KIỂM TRA */}
            <Section
              tour="up-checkset"
              eyebrow={t("up.step1")}
              icon={<IconStack className="h-5 w-5" />}
              title={t("up.setTitle")}
              desc={t("up.setDesc")}
              done={choiceDone}
              aside={
                <button type="button" className={BTN} disabled={loading} onClick={() => setDialog("checkset")}>
                  <IconPlus className="h-4.5 w-4.5" /> {t("up.newCheckSet")}
                </button>
              }
            >
              {usable.length ? (
                <>
                  <label className="mb-1.5 block text-[12.5px] font-bold text-slate-600" htmlFor="fs-pick">
                    {t("up.activeSet")}
                  </label>
                  <div className="relative">
                    <select
                      id="fs-pick"
                      className={FIELD + " h-12 cursor-pointer appearance-none pr-10 text-[15px] font-semibold"}
                      value={choiceDone ? fieldSetId : ""}
                      disabled={loading || switching}
                      onChange={(e) => switchFieldSet(e.target.value)}
                    >
                      {!choiceDone ? <option value="">{t("up.pickSet")}</option> : null}
                      {usable.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.display_name} — {s.fields} {t("up.fieldCount")}
                        </option>
                      ))}
                    </select>
                    <span className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-400">
                      {switching ? <Spinner dark /> : "▾"}
                    </span>
                  </div>
                  {chosen && choiceDone ? (
                    <div className="mt-3 flex items-start gap-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3">
                      <IconInfo className="mt-0.5 h-5 w-5 shrink-0 text-blue-700" />
                      <div className="min-w-0 text-[13px] leading-relaxed">
                        <div className="font-bold text-slate-800">
                          {chosen.display_name}
                          {chosen.document_kind ? <span className="font-semibold text-slate-500"> · {chosen.document_kind}</span> : null}
                        </div>
                        <div className="text-slate-600">
                          {chosen.description ? `${chosen.description} · ` : ""}
                          {chosen.fields} {t("up.fieldCount")} · {t("cs.regs")}:{" "}
                          {chosen.regulation_sets?.length ? chosen.regulation_sets.join(", ") : t("cs.allRegs")}
                        </div>
                      </div>
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 px-5 py-6 text-center">
                  <div className="text-[14.5px] font-bold text-slate-800">{t("up.dropLocked")}</div>
                  <p className="m-0 mx-auto mt-1 max-w-md text-[13px] text-slate-500">{t("up.dropLockedHint")}</p>
                  <button type="button" className={BTN_PRIMARY + " mt-4"} onClick={() => setDialog("checkset")}>
                    <IconPlus className="h-4.5 w-4.5" /> {t("up.newCheckSet")}
                  </button>
                </div>
              )}
            </Section>

            {/* 02 — TÀI LIỆU */}
            <Section
              tour="up-files"
              eyebrow={t("up.step2")}
              icon={<IconUpload className="h-5 w-5" />}
              title={t("up.files")}
              desc={t("up.filesDesc")}
              done={files.length > 0}
            >
              <div className={"grid gap-4 " + (showDrop ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" : "")}>
                {showDrop ? (
                <label
                  onDragOver={(e) => {
                    e.preventDefault();
                    if (choiceDone && !loading) setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragOver(false);
                    if (!loading && choiceDone) addFiles(e.dataTransfer.files);
                  }}
                  className={
                    "drop-zone flex min-h-[188px] flex-col items-center justify-center gap-2 px-5 py-6 text-center " +
                    (!choiceDone || loading ? "is-locked" : "cursor-pointer") + (dragOver ? " is-over" : "")
                  }
                >
                  <span className="grid h-12 w-12 place-items-center rounded-xl bg-blue-50 text-blue-700">
                    <IconUpload className="h-7 w-7" />
                  </span>
                  <span className="text-[15px] font-bold text-slate-800">
                    {choiceDone ? t("up.drop") : t("up.dropLocked")}
                  </span>
                  <span className="text-[12.5px] text-slate-500">
                    {choiceDone ? t("up.dropHint") : t("up.dropLockedHint")}
                  </span>
                  {choiceDone ? (
                    <span className="mt-1 inline-flex items-center gap-1.5 rounded-[9px] border border-blue-300 px-3.5 py-1.5 text-[13px] font-bold text-blue-700">
                      <IconFile className="h-4 w-4" /> {t("up.pickFiles")}
                    </span>
                  ) : null}
                  <span className="text-[11.5px] text-slate-400">
                    {t("up.limits").replace("{n}", String(MAX_FILES)).replace("{mb}", String(MAX_FILE_MB)).replace("{total}", String(MAX_TOTAL_MB))}
                  </span>
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    multiple
                    disabled={loading || !choiceDone}
                    className="hidden"
                    onChange={(e) => {
                      addFiles(e.target.files);
                      e.target.value = ""; // chọn lại đúng file vừa bỏ vẫn nhận
                    }}
                  />
                </label>
                ) : null}

                <div className="flex min-h-[188px] flex-col rounded-[14px] border border-slate-200 bg-slate-50/60">
                  <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
                    <span className="text-[13px] font-bold text-slate-700">
                      {t("up.chosen")} ({files.length})
                    </span>
                    {files.length && !loading ? (
                      <span className="flex items-center gap-3">
                        {canRemove ? (
                          <button
                            type="button"
                            onClick={() => { setFiles([]); setEditingFiles(false); }}
                            title={t("up.clearAllTitle")}
                            className="cursor-pointer border-0 bg-transparent p-0 text-[12.5px] font-bold text-red-600 hover:underline"
                          >
                            {t("up.clearAll")}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setEditingFiles((v) => !v)}
                          className={BTN + " !px-3 !py-1 text-[12.5px]"}
                        >
                          {editingFiles ? <><IconCheck className="h-4 w-4" /> {t("up.changeDone")}</> : <><IconEdit className="h-4 w-4" /> {t("up.change")}</>}
                        </button>
                      </span>
                    ) : null}
                  </div>
                  {files.length ? (
                    <ul className="m-0 flex max-h-[260px] list-none flex-col overflow-y-auto p-1.5">
                      {files.map((f, i) => (
                        <li
                          key={fileKey(f)}
                          className="rise-in flex items-center gap-3 rounded-[10px] px-2.5 py-2 hover:bg-slate-100"
                        >
                          <span className="grid h-9 w-8 shrink-0 place-items-center rounded-md bg-red-100 text-[9px] font-extrabold tracking-wide text-red-700">
                            PDF
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13.5px] font-semibold text-slate-800" title={f.name}>{f.name}</span>
                            <span className="block font-mono text-[11.5px] text-slate-500">{fmtSize(f.size)}</span>
                          </span>
                          {liveRegions[fileKey(f)] ? (
                            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-bold text-blue-700">
                              {t("up.hasRegion")}
                            </span>
                          ) : null}
                          {canRemove ? (
                            <button
                              type="button"
                              onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                              className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-lg border-0 bg-transparent text-slate-400 hover:bg-red-50 hover:text-red-600"
                              title={t("up.removeFile")}
                              aria-label={`${t("up.removeFile")}: ${f.name}`}
                            >
                              <IconX className="h-4.5 w-4.5" />
                            </button>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="grid flex-1 place-items-center px-4 py-6 text-center text-[13px] text-slate-400">
                      {t("up.noFiles")}
                    </div>
                  )}
                </div>
              </div>
            </Section>

            {/* 03 — TÙY CHỌN */}
            <Section
              tour="up-options"
              eyebrow={t("up.step3")}
              icon={<IconSliders className="h-5 w-5" />}
              title={t("up.mode")}
              desc={t("up.modeDesc")}
            >
              {/* Độ nét khi đọc nay nằm ở Cài đặt > Đọc tài liệu. */}
              <div className="grid gap-4">
                <div className="rounded-[14px] border border-slate-200 bg-slate-50/60 p-4">
                  <div className="flex items-start gap-3">
                    <IconCrop className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
                    <div className="min-w-0 flex-1">
                      <div className="text-[13.5px] font-bold text-slate-800">{t("up.region")}</div>
                      <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-slate-500">
                        {files.length ? t("up.regionTitle") : t("up.regionNeedFiles")}
                      </p>
                    </div>
                    <label className="switch" title={files.length ? t("up.regionTitle") : t("up.regionNeedFiles")}>
                      <input
                        type="checkbox"
                        role="switch"
                        aria-label={t("up.region")}
                        checked={regionsActive}
                        disabled={!files.length || loading}
                        onChange={toggleRegions}
                      />
                      <i />
                    </label>
                  </div>
                  {regionsActive ? (
                    <div className="mt-3 flex items-center gap-3 border-t border-slate-200 pt-3 text-[12.5px] text-slate-600">
                      <span className="flex-1">
                        {regionSummary.cropped} {t("up.regionCropped")}
                        {regionSummary.skipped ? ` · ${regionSummary.skipped} ${t("up.regionSkipped")}` : ""}
                      </span>
                      <button
                        type="button"
                        className={BTN + " !px-3 !py-1.5 text-xs"}
                        disabled={loading}
                        onClick={() => setRegionPickerOpen(true)}
                      >
                        {t("up.regionEdit")}
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            </Section>
          </div>

          {/* THANH HÀNH ĐỘNG — dính đáy; nói rõ còn thiếu gì, hoặc tiến độ khi đang chạy. */}
          <div className="action-bar mt-5 flex flex-wrap items-center gap-4 px-5 py-3.5" data-tour="up-submit">
            <span className={"grid h-9 w-9 shrink-0 place-items-center rounded-full " +
              (loading ? "bg-blue-50 text-blue-700" : readiness.ok ? "bg-green-50 text-green-600" : "bg-slate-100 text-slate-400")}>
              {loading ? <Spinner dark /> : readiness.ok ? <IconCheck className="h-5 w-5" /> : <IconInfo className="h-5 w-5" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-bold text-slate-800">
                {loading ? job.text || t("up.running") : readiness.title}
              </span>
              <span className="block truncate text-[12.5px] text-slate-500">
                {loading ? job.eta || t("up.runningHint") : readiness.desc}
              </span>
            </span>
            <button className={BTN_PRIMARY + " px-6"} disabled={loading || !canSubmit} onClick={onConfirm}>
              {loading ? (
                <>
                  <Spinner />
                  {t("up.running")}
                </>
              ) : (
                <>
                  {t("up.submit")} <IconArrowRight className="h-4.5 w-4.5" />
                </>
              )}
            </button>
          </div>
        </div>

        {/* CỘT PHẢI — quy trình + căn cứ đối chiếu của lượt này. */}
        <aside className="grid gap-5 xl:sticky xl:top-[88px]">
          <RailCard title={t("up.flowTitle")} icon={<IconCheck className="h-5 w-5" />}>
            <ol className="m-0 flex list-none flex-col gap-1 p-0">
              {[
                { label: t("step.upload"), state: loading ? "done" : "current" },
                { label: t("step.review"), state: loading ? "current" : "todo" },
                { label: t("step.result"), state: "todo" },
              ].map((s, i) => (
                <li
                  key={s.label}
                  className={"flex items-center gap-3 rounded-[10px] px-2.5 py-2.5 " + (s.state === "current" ? "bg-blue-50" : "")}
                >
                  <span className={"step-dot " + (s.state === "current" ? "is-current" : s.state === "done" ? "is-done" : "")}>
                    {s.state === "done" ? <IconCheck className="h-4 w-4" /> : i + 1}
                  </span>
                  <span className={"flex-1 text-[13.5px] " + (s.state === "todo" ? "font-semibold text-slate-500" : "font-bold text-slate-800")}>
                    {s.label}
                  </span>
                  <span className={"rounded-full px-2 py-0.5 text-[11px] font-bold " +
                    (s.state === "current" ? "bg-blue-100 text-blue-700" : s.state === "done" ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500")}>
                    {t(`up.flow.${s.state}`)}
                  </span>
                </li>
              ))}
            </ol>
          </RailCard>

          <RailCard
            title={t("up.regsTitle")}
            icon={<IconScale className="h-5 w-5" />}
            aside={<Link to="/bo-quy-dinh" className="text-[12.5px] font-bold text-blue-700 no-underline hover:underline">{t("up.manage")}</Link>}
          >
            {!choiceDone ? (
              <p className="m-0 text-[13px] text-slate-500">{t("up.regsNoSet")}</p>
            ) : linkedRegs.length ? (
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {linkedRegs.map((r) => (
                  <li key={r.name} className="flex items-start gap-2.5">
                    <IconCheck className="mt-0.5 h-4.5 w-4.5 shrink-0 text-green-600" />
                    <span className="min-w-0 flex-1 text-[13px]">
                      <span className="block font-semibold text-slate-800">{r.name}</span>
                      <span className="block text-[12px] text-slate-500">
                        {r.documents ? `${r.documents} ${t("rset.docs")}` : t("up.regsMissing")}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-[13px]">
                <div className="font-semibold text-slate-800">{t("up.regsAll")}</div>
                <div className="mt-0.5 text-[12px] text-slate-500">
                  {regSets.length} {t("up.regsSetUnit")} · {allDocs} {t("rset.docs")}
                </div>
              </div>
            )}
          </RailCard>
        </aside>
      </div>

      {regionPickerOpen && files.length ? (
        <RegionPicker
          files={files}
          initial={liveRegions}
          onCancel={() => {
            setRegionPickerOpen(false);
            // Hủy ngay lần bật đầu (chưa có vùng nào) -> tắt lại công tắc.
            if (!Object.keys(liveRegions).length) setRegionsOn(false);
          }}
          onConfirm={(r) => {
            setRegions(r);
            setRegionPickerOpen(false);
            setRegionsOn(Object.keys(r).length > 0);
          }}
        />
      ) : null}
      {dialog === "checkset" ? (
        <CheckSetDialog
          activeId={fieldSetId}
          onClose={() => setDialog("")}
          onChanged={() => loadFieldSets()}
        />
      ) : null}
      {dialog === "regset" ? (
        <RegulationSetDialog
          onClose={() => {
            setDialog("");
            loadRegSets();
          }}
        />
      ) : null}
    </AppShell>
  );
}
