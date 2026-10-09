import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import type { FileRegion } from "../types";
import { Spinner } from "./Layout";
import { BTN, BTN_PRIMARY, fileKey } from "../ui";
import { useT } from "../i18n";

type Rect = [number, number, number, number];
/** Vùng đang soạn của MỘT file: số trang + trang bỏ qua + vùng theo trang. */
type Draft = { pages: number; skip: Set<number>; rects: Map<number, Rect> };

// pdf.js (~1 MB) chỉ nạp khi người dùng MỞ cửa sổ chọn vùng, không đè lên lần mở trang.
let _pdfjs: Promise<typeof import("pdfjs-dist")> | null = null;
function loadPdfjs() {
  _pdfjs ??= Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
  ]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = worker.default;
    return lib;
  });
  return _pdfjs;
}

/**
 * CỬA SỔ CHỌN VÙNG CẦN KIỂM TRA — chiếm 90% trang web.
 *
 *  · Giữa: trang PDF đang xem; kéo chuột để khoanh MỘT hình chữ nhật trên trang (vẽ lại
 *    là thay vùng cũ). Hệ thống chỉ đọc phần trong vùng của trang đó.
 *  · Sidebar phải: mỗi trang một dòng — ô tick "Quét trang" + "Trang N" (bấm để xem
 *    trang); trang đã khoanh vùng hiện "Đã chọn". Trang bỏ tick không được đọc.
 *  · Dưới cùng, sát phải: Hủy bỏ (trái) — Xác nhận (phải).
 *
 * Tọa độ gửi đi chuẩn hóa 0..1 theo trang (gốc trên-trái) nên không phụ thuộc kích
 * thước hiển thị hay DPI đọc ảnh ở backend.
 */
export default function RegionPicker({
  files,
  initial,
  onCancel,
  onConfirm,
}: {
  files: File[];
  initial: Record<string, FileRegion>;
  onCancel: () => void;
  onConfirm: (regions: Record<string, FileRegion>) => void;
}) {
  const t = useT();
  const [docs, setDocs] = useState<(PDFDocumentProxy | null)[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loadError, setLoadError] = useState("");
  const [cur, setCur] = useState<{ file: number; page: number }>({ file: 0, page: 0 });

  // Nạp mọi file PDF (pdf.js chạy trong worker) + dựng bản nháp từ vùng đã xác nhận trước.
  useEffect(() => {
    let alive = true;
    const tasks: { destroy: () => Promise<void> }[] = [];
    loadPdfjs()
      .then((lib) =>
        Promise.all(
          files.map(async (f) => {
            try {
              const task = lib.getDocument({ data: new Uint8Array(await f.arrayBuffer()) });
              tasks.push(task);
              return await task.promise;
            } catch {
              return null;
            }
          }),
        ),
      )
      .then((list) => {
        if (!alive) return;
        setDocs(list);
        setDrafts(
          list.map((d, i) => {
            const prev = initial[fileKey(files[i])];
            const pages = d?.numPages ?? 0;
            return {
              pages,
              skip: new Set((prev?.skip ?? []).filter((p) => p < pages)),
              rects: new Map(
                Object.entries(prev?.rects ?? {})
                  .map(([k, r]) => [Number(k), r] as [number, Rect])
                  .filter(([k]) => k < pages),
              ),
            };
          }),
        );
        if (list.every((d) => !d)) setLoadError(t("rg.loadFailed"));
      })
      .catch(() => alive && setLoadError(t("rg.loadFailed")));
    return () => {
      alive = false;
      tasks.forEach((tk) => void tk.destroy());
    };
    // Chỉ nạp lại khi đổi DANH SÁCH file — vùng `initial` chỉ dùng làm điểm xuất phát.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files]);

  const draft = drafts[cur.file];
  const doc = docs[cur.file];
  const rect = draft?.rects.get(cur.page) ?? null;

  function update(fileIdx: number, fn: (d: Draft) => Draft) {
    setDrafts((prev) => prev.map((d, i) => (i === fileIdx ? fn(d) : d)));
  }

  function setRect(r: Rect | null) {
    update(cur.file, (d) => {
      const rects = new Map(d.rects);
      const skip = new Set(d.skip);
      if (r) {
        rects.set(cur.page, r);
        skip.delete(cur.page); // khoanh vùng trên trang = trang đó phải được quét
      } else rects.delete(cur.page);
      return { ...d, rects, skip };
    });
  }

  function toggleScan(fileIdx: number, page: number) {
    update(fileIdx, (d) => {
      const skip = new Set(d.skip);
      if (skip.has(page)) skip.delete(page);
      else skip.add(page);
      return { ...d, skip };
    });
  }

  // File nào bỏ tick HẾT trang thì không xác nhận được — không còn gì để đọc.
  const emptyFiles = drafts
    .map((d, i) => (d.pages > 0 && d.skip.size >= d.pages ? files[i].name : ""))
    .filter(Boolean);

  function confirm() {
    const out: Record<string, FileRegion> = {};
    drafts.forEach((d, i) => {
      if (!d.skip.size && !d.rects.size) return;
      out[fileKey(files[i])] = {
        skip: [...d.skip].sort((a, b) => a - b),
        rects: Object.fromEntries(d.rects),
      };
    });
    onConfirm(out);
  }

  // Esc = Hủy bỏ.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onCancel]);

  const loading = !docs.length && !loadError;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label={t("rg.title")}
    >
      {/* Cửa sổ nhỏ = 90% diện tích trang web (90% rộng × 90% cao). */}
      <div className="flex h-[90vh] w-[90vw] flex-col overflow-hidden rounded-xl border border-slate-200 bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
          <div>
            <div className="text-[15px] font-bold text-slate-800">{t("rg.title")}</div>
            <div className="text-xs text-slate-500">{t("rg.hint")}</div>
          </div>
          {rect ? (
            <button type="button" className={BTN + " !px-3 !py-1.5 text-xs"} onClick={() => setRect(null)}>
              {t("rg.clearRect")}
            </button>
          ) : null}
        </div>

        <div className="flex min-h-0 flex-1">
          {/* Trang đang xem */}
          <div className="relative min-w-0 flex-1 bg-slate-100">
            {loading ? (
              <div className="grid h-full place-items-center text-sm text-slate-500">
                <span className="flex items-center gap-2"><Spinner dark /> {t("common.loading")}</span>
              </div>
            ) : loadError ? (
              <div className="grid h-full place-items-center p-6 text-sm text-red-600">{loadError}</div>
            ) : doc && draft ? (
              <PageCanvas
                key={`${cur.file}:${cur.page}`}
                doc={doc}
                page={cur.page}
                rect={rect}
                skipped={draft.skip.has(cur.page)}
                onRect={setRect}
                skippedText={t("rg.skippedPage")}
              />
            ) : (
              <div className="grid h-full place-items-center p-6 text-sm text-red-600">
                {t("rg.fileFailed")}
              </div>
            )}
          </div>

          {/* Sidebar phải: danh sách trang */}
          <aside className="flex w-60 shrink-0 flex-col border-l border-slate-200 bg-surface">
            <div className="flex-1 overflow-y-auto p-2">
              {files.map((f, fi) => {
                const d = drafts[fi];
                return (
                  <div key={fileKey(f)} className="mb-2">
                    {files.length > 1 ? (
                      <div className="truncate px-1.5 pb-1 pt-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-500" title={f.name}>
                        {f.name}
                      </div>
                    ) : null}
                    {!d ? null : d.pages === 0 ? (
                      <div className="px-1.5 text-xs text-red-600">{t("rg.fileFailed")}</div>
                    ) : (
                      Array.from({ length: d.pages }, (_, p) => {
                        const active = cur.file === fi && cur.page === p;
                        const scan = !d.skip.has(p);
                        return (
                          <div
                            key={p}
                            className={
                              "mb-1 rounded-md border px-2 py-1.5 " +
                              (active ? "border-blue-500 bg-blue-50" : "border-slate-200 hover:bg-slate-50")
                            }
                          >
                            <div className="flex items-center gap-2 text-[13px]">
                              <input
                                type="checkbox"
                                checked={scan}
                                onChange={() => toggleScan(fi, p)}
                                title={t("rg.scanPage")}
                                aria-label={`${t("rg.scanPage")} ${t("rg.page")} ${p + 1}`}
                              />
                              <span className="text-xs text-slate-500">{t("rg.scanPage")}</span>
                              <button
                                type="button"
                                onClick={() => setCur({ file: fi, page: p })}
                                className={
                                  "ml-auto bg-transparent p-0 font-semibold " +
                                  (active ? "text-blue-700" : "text-slate-700 hover:text-blue-600")
                                }
                              >
                                {t("rg.page")} {p + 1}
                              </button>
                            </div>
                            {d.rects.has(p) ? (
                              <div className="mt-0.5 text-right text-[11px] font-semibold text-green-700">
                                {t("rg.selected")}
                              </div>
                            ) : null}
                          </div>
                        );
                      })
                    )}
                  </div>
                );
              })}
            </div>
          </aside>
        </div>

        {/* Hủy bỏ (trái) — Xác nhận (phải), sát lề phải cửa sổ. */}
        <div className="flex items-center gap-3 border-t border-slate-200 px-4 py-2.5">
          <span className="flex-1 text-xs text-red-600">
            {emptyFiles.length ? `${t("rg.errNoPage")}: ${emptyFiles.join(", ")}` : ""}
          </span>
          <button type="button" className={BTN} onClick={onCancel}>
            {t("rg.cancel")}
          </button>
          <button
            type="button"
            className={BTN_PRIMARY}
            onClick={confirm}
            disabled={loading || !!loadError || emptyFiles.length > 0}
          >
            {t("rg.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Một trang PDF vừa khung + lớp khoanh vùng bằng chuột. */
function PageCanvas({
  doc,
  page,
  rect,
  skipped,
  skippedText,
  onRect,
}: {
  doc: PDFDocumentProxy;
  page: number;
  rect: Rect | null;
  skipped: boolean;
  skippedText: string;
  onRect: (r: Rect | null) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

  // Vẽ trang vừa khung (giữ tỉ lệ), nét theo mật độ điểm ảnh của màn hình.
  useEffect(() => {
    let task: RenderTask | null = null;
    let alive = true;
    doc.getPage(page + 1).then((pg) => {
      const wrap = wrapRef.current;
      const canvas = canvasRef.current;
      if (!alive || !wrap || !canvas) return;
      const base = pg.getViewport({ scale: 1 });
      const scale = Math.min((wrap.clientWidth - 32) / base.width, (wrap.clientHeight - 32) / base.height);
      const vp = pg.getViewport({ scale: Math.max(0.1, scale) });
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(vp.width * ratio);
      canvas.height = Math.floor(vp.height * ratio);
      canvas.style.width = `${Math.floor(vp.width)}px`;
      canvas.style.height = `${Math.floor(vp.height)}px`;
      setSize({ w: Math.floor(vp.width), h: Math.floor(vp.height) });
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      task = pg.render({
        canvasContext: ctx,
        viewport: vp,
        transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
      });
      task.promise.catch(() => {});
    });
    return () => {
      alive = false;
      task?.cancel();
    };
  }, [doc, page]);

  const norm = useCallback(
    (e: React.PointerEvent) => {
      const box = canvasRef.current!.getBoundingClientRect();
      const x = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
      const y = Math.min(1, Math.max(0, (e.clientY - box.top) / box.height));
      return { x, y };
    },
    [],
  );

  const shown: Rect | null = useMemo(() => {
    if (drag) {
      return [
        Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1),
        Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1),
      ];
    }
    return rect;
  }, [drag, rect]);

  return (
    <div ref={wrapRef} className="absolute inset-0 grid place-items-center overflow-hidden">
      <div className="relative shadow-lg" style={size ? { width: size.w, height: size.h } : undefined}>
        <canvas ref={canvasRef} className={"block bg-surface " + (skipped ? "opacity-40" : "")} />
        {/* Lớp khoanh vùng: kéo chuột để vẽ hình chữ nhật. */}
        <div
          className="absolute inset-0 cursor-crosshair touch-none select-none"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            const p = norm(e);
            setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
          }}
          onPointerMove={(e) => {
            if (!drag) return;
            const p = norm(e);
            setDrag({ ...drag, x1: p.x, y1: p.y });
          }}
          onPointerUp={() => {
            if (!drag) return;
            const r: Rect = [
              Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1),
              Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1),
            ];
            setDrag(null);
            // Cú bấm (không kéo) hoặc vùng quá nhỏ: bỏ qua, giữ vùng cũ.
            if (r[2] - r[0] >= 0.01 && r[3] - r[1] >= 0.01) onRect(r);
          }}
        >
          {shown ? (
            <>
              {/* Phủ mờ phần NGOÀI vùng để thấy rõ phần sẽ được đọc. */}
              <div
                className="pointer-events-none absolute border-2 border-blue-600 bg-blue-500/10"
                style={{
                  left: `${shown[0] * 100}%`,
                  top: `${shown[1] * 100}%`,
                  width: `${(shown[2] - shown[0]) * 100}%`,
                  height: `${(shown[3] - shown[1]) * 100}%`,
                  boxShadow: "0 0 0 9999px rgba(15, 23, 42, 0.35)",
                }}
              />
            </>
          ) : null}
        </div>
        {skipped ? (
          <div className="pointer-events-none absolute inset-x-0 top-3 text-center">
            <span className="rounded bg-black/70 px-2 py-1 text-xs font-semibold text-white">{skippedText}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
