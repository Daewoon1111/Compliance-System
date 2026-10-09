import { useCallback, useEffect, useRef, useState } from "react";
import {
  configDelete, configFieldSets, configRead, configTemplate, configValidate, configWrite,
  friendly, getActiveFieldSet, setActiveFieldSet,
} from "../api/client";
import type { FieldSetInfo } from "../types";
import { AppShell, Alert, PageHeader, Spinner } from "../components/Layout";
import { IconCheck, IconEdit, IconPlus, IconStack, IconUpload, IconWarning } from "../components/Icons";
import CheckSetDialog from "../components/CheckSetDialog";
import { notify } from "../notify";
import { CARD, BTN, BTN_GHOST, BTN_PRIMARY, FIELD } from "../ui";
import { useT } from "../i18n";

/** Mã gợi ý từ tên hiển thị / tên file: chữ thường, bỏ dấu, chỉ a-z 0-9 _ -. */
function slug(s: string): string {
  return s
    .replace(/đ/g, "d").replace(/Đ/g, "D")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

/**
 * TRANG BỘ KIỂM TRA (của NGƯỜI DÙNG — không cần mã quản trị).
 *
 * Mỗi bộ kiểm tra mô tả MỘT loại hồ sơ — thông tin nào cần trích xuất, nhận biết trong
 * văn bản ra sao, kiểm tra theo tiêu chí gì, đối chiếu với bộ quy định nào. Danh sách
 * hiện CẢ bộ mặc định (chỉ đọc — sao chép làm bộ mới) và bộ của người dùng (sửa được).
 *   · Tạo / Sửa / Sao chép: trình tạo 4 bước (CheckSetDialog) — không phải viết JSON.
 *   · JSON: bảng soạn thô cho thuộc tính nâng cao (biểu thức nhận dạng, độ dài tối đa…),
 *     có kiểm tra hợp lệ trực tiếp với backend.
 * Bộ mặc định chỉ sửa được ở trang Quản trị.
 */
export default function Config() {
  const t = useT();
  const [items, setItems] = useState<FieldSetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Bảng soạn: `open` = đang mở, `editing` = mã bộ của người dùng đang sửa ("" = tạo mới).
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState("");
  const [dragOver, setDragOver] = useState(false);
  // KIỂM TRA SỚM nội dung đang soạn: báo sau khi lưu thì người nhập đã đi tiếp.
  const [problems, setProblems] = useState<string[]>([]);
  const [jsonError, setJsonError] = useState(false);
  // BỘ ĐANG DÙNG ở trang Kiểm tra + hộp thoại tạo/sửa (trình tạo 4 bước).
  const [activeId, setActiveId] = useState("");
  const [wizard, setWizard] = useState<{ editId?: string; copyFrom?: string } | null>(null);
  const editorRef = useRef<HTMLDivElement | null>(null);

  // Mở bảng soạn JSON (nằm dưới danh sách) -> cuộn tới đó, không để người dùng tưởng
  // bấm nút mà không có gì xảy ra.
  useEffect(() => {
    if (open) editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [open]);

  const refresh = useCallback(
    () => {
      getActiveFieldSet().then((a) => setActiveId(a.field_set || "")).catch(() => {});
      return configFieldSets()
        .then((d) => { setItems(d.field_sets || []); setError(""); })
        .catch((e) => setError(friendly(e)));
    },
    [],
  );

  function makeActive(id: string) {
    setActiveFieldSet(id)
      .then((a) => { setActiveId(a.field_set || id); notify("success", t("cf.nowActive")); })
      .catch((e) => notify("error", friendly(e)));
  }

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  function closeEditor() {
    setOpen(false);
    setEditing("");
    setId("");
    setContent("");
    setDirty(false);
    setError("");
  }

  function newFieldSet() {
    setError("");
    setEditing("");
    setId("");
    setDirty(false);
    setOpen(true);
    configTemplate()
      .then((d) => setContent(d.content))
      .catch((e) => setError(friendly(e)));
  }

  /** Bộ của người dùng -> sửa tại chỗ. Bộ mặc định -> mở bản SAO để lưu thành bộ mới. */
  function openFieldSet(it: FieldSetInfo) {
    setError("");
    setDirty(false);
    setOpen(true);
    const own = it.source === "user";
    setEditing(own ? it.id : "");
    setId(own ? it.id : `${it.id}_ban_sao`);
    configRead(it.id)
      .then((d) => setContent(d.content))
      .catch((e) => setError(friendly(e)));
  }

  // Gõ tới đâu soi tới đó, hoãn 500 ms: mỗi phím một request là vô ích, còn soi ở
  // frontend thì hai bên sẽ trôi khỏi nhau — luật hợp lệ là của backend.
  useEffect(() => {
    let alive = true;
    const off = !open || !content.trim();
    const timer = setTimeout(() => {
      if (off) {
        if (alive) { setProblems([]); setJsonError(false); }
        return;
      }
      configValidate(content)
        .then((r) => { if (alive) { setProblems(r.problems || []); setJsonError(r.json_error); } })
        .catch(() => { if (alive) { setProblems([]); setJsonError(false); } });
    }, off ? 0 : 500);
    return () => { alive = false; clearTimeout(timer); };
  }, [open, content]);

  /** Nạp nội dung từ FILE .json người dùng tải lên — vào thẳng ô soạn để soát trước
   *  khi lưu. Tên file thành mã gợi ý nếu ô mã đang trống. */
  function readFile(file: File) {
    file.text().then((txt) => {
      setContent(txt);
      setDirty(true);
      setError("");
      if (!id.trim()) setId(slug(file.name.replace(/\.json$/i, "")));
    }).catch((e) => setError(t("cf.readFailed") + " " + String(e)));
  }

  async function save() {
    const fid = id.trim();
    if (!fid) return setError(t("cf.needId"));
    try { JSON.parse(content); }
    catch (e) { return setError(t("cf.badJson") + " " + (e instanceof Error ? e.message : String(e))); }
    setSaving(true);
    setError("");
    try {
      const res = await configWrite(fid, content);
      notify("success", res.note);
      await refresh();
      closeEditor();   // lưu xong -> ĐÓNG bảng soạn, chỉ còn danh sách
    } catch (e) {
      const m = friendly(e);
      setError(m);
      notify("error", t("cf.saveFailed") + " " + m);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    // Xóa là thao tác KHÔNG HOÀN TÁC và nút Xóa nằm cạnh nút Lưu -> hỏi lại một lần.
    if (!editing || !window.confirm(t("cf.deleteConfirm"))) return;
    setSaving(true);
    try {
      const res = await configDelete(editing);
      notify("success", res.note);
      await refresh();
      closeEditor();
    } catch (e) {
      notify("error", t("cf.deleteFailed") + " " + friendly(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <AppShell>
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> {t("cf.loading")}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title={t("cf.title")}
        desc={t("cf.lead")}
        actions={
          <>
            <button className={BTN_GHOST} onClick={newFieldSet} disabled={open} title={t("cf.jsonNewTitle")}>
              {"{ }"} {t("cf.jsonNew")}
            </button>
            <button className={BTN_PRIMARY} onClick={() => setWizard({})} disabled={open}>
              <IconPlus className="h-4.5 w-4.5" /> {t("up.newCheckSet")}
            </button>
          </>
        }
      />

      {error ? <Alert kind="error">{error}</Alert> : null}

      {/* DANH SÁCH bộ kiểm tra — thẻ; bộ đang dùng có viền nhấn. */}
      {items.length ? (
        <div className={"grid gap-4 lg:grid-cols-2 2xl:grid-cols-3" + (open ? " mb-5" : "")}>
          {items.map((it) => {
            const own = it.source === "user";
            const usable = !it.error && it.fields > 0;
            const on = it.id === activeId;
            return (
              <article
                key={it.id}
                className={"surface-card flex flex-col p-5 " + (on ? "!border-blue-500 ring-1 ring-blue-500/40" : "")}
              >
                <div className="flex items-start gap-3.5">
                  <span className={"grid h-11 w-11 shrink-0 place-items-center rounded-xl border " +
                    (on ? "border-blue-300 bg-blue-600 text-white" : "border-slate-200 bg-slate-100 text-slate-500")}>
                    <IconStack className="h-5.5 w-5.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="m-0 truncate text-[16px] font-extrabold text-slate-800" title={it.display_name}>{it.display_name}</h3>
                    <div className="mt-0.5 truncate font-mono text-[11.5px] text-slate-400">{it.id}</div>
                  </div>
                  <span className={"shrink-0 rounded-full px-2.5 py-0.5 text-[11.5px] font-bold " +
                    (own ? "bg-blue-50 text-blue-700" : "bg-slate-100 text-slate-500")}>
                    {own ? t("cf.sourceUser") : t("cf.sourceDefault")}
                  </span>
                </div>

                <dl className="m-0 mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
                  <dt className="text-slate-500">{t("cf.colFields")}</dt>
                  <dd className="m-0 font-semibold text-slate-800">{it.fields}</dd>
                  {it.document_kind ? (
                    <>
                      <dt className="text-slate-500">{t("cs.kind")}</dt>
                      <dd className="m-0 truncate text-slate-800">{it.document_kind}</dd>
                    </>
                  ) : null}
                  <dt className="text-slate-500">{t("cs.regs")}</dt>
                  <dd className="m-0 truncate text-slate-800" title={it.regulation_sets?.join(", ")}>
                    {it.regulation_sets?.length ? it.regulation_sets.join(", ") : t("up.regsAll")}
                  </dd>
                </dl>
                {it.description ? <p className="m-0 mt-2.5 line-clamp-2 text-[12.5px] text-slate-500">{it.description}</p> : null}
                {it.error ? (
                  <p className="m-0 mt-2.5 flex items-start gap-1.5 text-[12.5px] text-red-600">
                    <IconWarning className="h-4.5 w-4.5 shrink-0" /> {it.error}
                  </p>
                ) : null}

                <div className="mt-auto pt-4">
                <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3.5">
                  {on ? (
                    <span className="mr-auto inline-flex items-center gap-1.5 text-[12.5px] font-bold text-blue-700">
                      <IconCheck className="h-4.5 w-4.5" /> {t("cs.inUse")}
                    </span>
                  ) : usable ? (
                    <button type="button" className="mr-auto cursor-pointer border-0 bg-transparent p-0 text-[12.5px] font-bold text-blue-700 hover:underline"
                      onClick={() => makeActive(it.id)} disabled={open}>
                      {t("cs.use")}
                    </button>
                  ) : <span className="mr-auto" />}
                  <button type="button" className={BTN_GHOST + " !px-3 !py-1.5 text-xs"} disabled={open}
                    onClick={() => openFieldSet(it)} title={own ? t("cf.editThis") : t("cf.copyThis")}>
                    {"{ }"} JSON
                  </button>
                  {own ? (
                    <button type="button" className={BTN + " !px-3 !py-1.5 text-xs"} disabled={open || !!it.error}
                      onClick={() => setWizard({ editId: it.id })}>
                      <IconEdit className="h-4 w-4" /> {t("cf.edit")}
                    </button>
                  ) : (
                    <button type="button" className={BTN + " !px-3 !py-1.5 text-xs"} disabled={open || !usable}
                      onClick={() => setWizard({ copyFrom: it.id })}>
                      <IconPlus className="h-4 w-4" /> {t("cf.copy")}
                    </button>
                  )}
                </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : !open ? (
        <div className="surface-card px-6 py-12 text-center">
          <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-blue-50 text-blue-700">
            <IconStack className="h-7 w-7" />
          </span>
          <div className="mt-4 text-[16px] font-extrabold text-slate-800">{t("cf.noneOwn")}</div>
          <button type="button" className={BTN_PRIMARY + " mt-5"} onClick={() => setWizard({})}>
            <IconPlus className="h-4.5 w-4.5" /> {t("up.newCheckSet")}
          </button>
        </div>
      ) : null}

      {/* BẢNG SOẠN — chỉ hiện khi đang tạo mới hoặc sửa; lưu xong là đóng. */}
      {open ? (
        <div ref={editorRef} className={CARD + " scroll-mt-24"}>
          <div className="mb-3 text-sm font-bold text-slate-800">
            {editing ? `${t("cf.editOne")} ${editing}` : t("cf.newTitle")}
          </div>

          <label className="block">
            <span className="block text-[13px] font-medium text-slate-600">{t("cf.idLabel")}</span>
            <input
              value={id}
              disabled={!!editing}
              onChange={(e) => { setId(e.target.value); setDirty(true); }}
              placeholder={t("cf.idPlaceholder")}
              className={FIELD + " mt-1 sm:max-w-md"}
            />
            <span className="mt-1 block text-xs text-slate-500">{t("cf.idNote")}</span>
          </label>

          <label className="mt-3 block text-[13px] font-medium text-slate-600">{t("cf.content")}</label>
          <textarea
            value={content}
            onChange={(e) => { setContent(e.target.value); setDirty(true); }}
            spellCheck={false}
            className="mt-1 h-[50vh] w-full resize-y rounded-lg border border-slate-200 p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500/30"
          />
          <div className="mt-1 text-xs text-slate-500">{t("cf.contentNote")}</div>

          {jsonError ? (
            <Alert kind="warn">{t("cf.jsonTyping")}</Alert>
          ) : problems.length ? (
            <Alert kind="warn">
              <b>{t("cf.warnTitle")}</b>
              <ul className="mt-1 mb-0 list-disc pl-5">
                {problems.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </Alert>
          ) : content.trim() ? (
            <div className="mt-2 text-xs font-medium text-green-700">{t("cf.valid")}</div>
          ) : null}

          {/* VÙNG TẢI FILE — CẢ VÙNG là <label> nên bấm chỗ nào cũng mở hộp chọn file. */}
          <label
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const f = e.dataTransfer.files?.[0];
              if (f) readFile(f);
            }}
            className={"mt-3 flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors " +
              (dragOver ? "border-blue-500 bg-blue-50" : "border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-blue-50/50")}
          >
            <IconUpload className="h-7 w-7 text-slate-400" />
            <span className="text-sm font-semibold text-blue-600">{t("cf.uploadBtn")}</span>
            <span className="text-xs text-slate-500">{t("cf.dropHint")}</span>
            <input
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) readFile(f); }}
            />
          </label>

          <div className="mt-4 flex flex-wrap items-center justify-end gap-3">
            {dirty ? <span className="mr-auto text-xs text-amber-600">{t("cf.unsaved")}</span> : null}
            {editing ? (
              <button className={BTN} onClick={remove} disabled={saving}>{t("common.delete")}</button>
            ) : null}
            <button className={BTN} onClick={closeEditor} disabled={saving}>{t("common.cancel")}</button>
            <button
              className={BTN_PRIMARY}
              onClick={save}
              disabled={saving || !content.trim() || !id.trim() || jsonError || problems.length > 0}
            >
              {saving ? t("common.saving") : t("cf.saveBtn")}
            </button>
          </div>
        </div>
      ) : null}

      {wizard ? (
        <CheckSetDialog
          activeId={activeId}
          editId={wizard.editId}
          copyFrom={wizard.copyFrom}
          onClose={() => setWizard(null)}
          onChanged={(id) => { setActiveId(id); refresh(); }}
        />
      ) : null}
    </AppShell>
  );
}
