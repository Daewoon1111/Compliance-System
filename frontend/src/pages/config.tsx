import { useCallback, useEffect, useState } from "react";
import {
  configApply, configDelete, configItems, configLint, configRead, configTemplate, configWrite,
  friendly,
} from "../api/client";
import type { UserConfigItem, UserConfigKind } from "../types";
import { AppShell, Alert, Spinner } from "../components/Layout";
import { IconNote, IconToggle, IconUpload } from "../components/Icons";
import { notify } from "../notify";
import { CARD, BTN, BTN_PRIMARY, FIELD } from "../ui";
import { useT, translate } from "../i18n";

const kindLabel = (k: UserConfigKind) => translate(k === "jobs" ? "cf.jobsTitle" : "cf.marketsTitle");
const kindHint = (k: UserConfigKind) => translate(k === "jobs" ? "cf.jobsDesc" : "cf.marketsDesc");

/**
 * TRANG CẤU HÌNH (của NGƯỜI DÙNG — không cần mã quản trị).
 *
 * Bố cục: DANH SÁCH cấu hình đã lưu (mỗi dòng: số thứ tự + mã cấu hình bên trái,
 * công tắc Áp dụng sát phải) + nút "Tạo cấu hình mới" mở bảng soạn. Lưu xong là
 * bảng soạn ĐÓNG LẠI, chỉ còn danh sách — không dùng ô chọn thả xuống nữa vì
 * danh sách đã hiện sẵn mọi cấu hình, thêm một lớp chọn nữa là thừa.
 *
 * Cấu hình mặc định của hệ thống chỉ sửa được ở trang Quản trị.
 */
export default function Config() {
  const t = useT();
  const [items, setItems] = useState<UserConfigItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Bảng soạn: `open` = đang mở, `editing` = mã cấu hình đang sửa ("" = tạo mới).
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<UserConfigKind>("jobs");
  const [id, setId] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [busyKey, setBusyKey] = useState(""); // dòng đang gạt công tắc
  // CẢNH BÁO SỚM về quy ước đặt tên. Báo sau khi lưu thì người nhập đã đi tiếp; cảnh
  // báo lúc đó chỉ còn là thông báo, không còn là cơ hội sửa.
  const [warnings, setWarnings] = useState<string[]>([]);

  const refresh = useCallback(
    () =>
      configItems()
        .then((d) => { setItems(d.items || []); setError(""); })
        // Danh sách RỖNG là chuyện bình thường (chưa tạo cấu hình nào) nên chỉ báo
        // lỗi khi THẬT SỰ gọi hỏng; 404 = backend chưa có route -> nói rõ phải làm gì.
        .catch((e) => setError(friendly(e))),
    [],
  );

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

  function newConfig() {
    setError("");
    setEditing("");
    setId("");
    setKind("jobs");
    setDirty(false);
    setOpen(true);
    loadTemplate("jobs");
  }

  function editConfig(it: UserConfigItem) {
    setError("");
    setKind(it.kind);
    setId(it.id);
    setEditing(it.id);
    setDirty(false);
    setOpen(true);
    configRead(it.kind, it.id)
      .then((d) => setContent(d.content))
      .catch((e) => setError(friendly(e)));
  }

  function loadTemplate(k: UserConfigKind) {
    configTemplate(k)
      .then((d) => { setContent(d.content); setDirty(false); })
      .catch((e) => setError(friendly(e)));
  }

  // Gõ tới đâu soi tới đó, hoãn 500 ms: mỗi phím một request là vô ích, còn soi ở
  // frontend thì hai bên sẽ trôi khỏi nhau — quy ước tên là của backend.
  useEffect(() => {
    let alive = true;
    // Mọi setState nằm TRONG timeout (chạy sau, bất đồng bộ) — đặt thẳng trong thân
    // effect là render dây chuyền, và eslint chặn đúng chỗ đó.
    const off = !open || kind !== "markets" || !content.trim();
    const timer = setTimeout(() => {
      if (off) { if (alive) setWarnings([]); return; }
      configLint(kind, content)
        .then((r) => { if (alive) setWarnings(r.warnings || []); })
        .catch(() => { if (alive) setWarnings([]); });
    }, off ? 0 : 500);
    return () => { alive = false; clearTimeout(timer); };
  }, [open, kind, content]);

  function onChangeKind(k: UserConfigKind) {
    setKind(k);
    loadTemplate(k);
  }

  /** Nạp nội dung từ FILE người dùng tải lên (.json cấu hình / .md văn bản) — nội
   *  dung vào thẳng ô soạn để soát trước khi lưu. Tên file thành mã gợi ý nếu trống. */
  function readConfigFile(file: File) {
    file.text().then((txt) => {
      setContent(txt);
      setDirty(true);
      setError("");
      if (!id.trim()) {
        setId(file.name.replace(/\.(json|md)$/i, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "_"));
      }
    }).catch((e) => setError(t("cf.readFailed") + " " + String(e)));
  }

  async function save() {
    const cid = id.trim();
    if (!cid) return setError(t("cf.needId"));
    try { JSON.parse(content); }
    catch (e) { return setError(t("cf.badJson") + " " + (e instanceof Error ? e.message : String(e))); }
    setSaving(true);
    setError("");
    try {
      const res = await configWrite(kind, cid, content);
      notify("success", res.note);
      for (const w of res.warnings || []) notify("error", w);
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

  async function toggleApply(it: UserConfigItem) {
    setBusyKey(it.kind + it.id);
    setError("");
    try {
      const res = await configApply(it.kind, it.id, !it.applied);
      notify("success", res.note);
      await refresh();
    } catch (e) {
      const m = friendly(e);
      setError(m);
      notify("error", t("cf.applyFailed") + " " + m);
    } finally {
      setBusyKey("");
    }
  }

  async function remove() {
    // Xóa cấu hình là thao tác KHÔNG HOÀN TÁC (file bị unlink ở backend) và nút Xóa
    // nằm ngay cạnh nút Lưu -> hỏi lại một lần trước khi gọi API.
    if (!editing || !window.confirm(t("cf.deleteConfirm"))) return;
    setSaving(true);
    try {
      const res = await configDelete(kind, editing);
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
      <div className={CARD + " mb-4"}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="m-0 text-lg font-bold">{t("cf.editTitle")}</h2>
            <p className="mt-1 mb-0 text-sm text-slate-500">
              {t("cf.jobFields")} {t("cf.and")} {t("cf.marketList")}.
            </p>
          </div>
          <button className={BTN_PRIMARY + " shrink-0 gap-2"} onClick={newConfig} disabled={open}>
            <IconNote className="h-5 w-5" />
            {t("cf.new")}
          </button>
        </div>
      </div>

      {error ? <Alert kind="error">{error}</Alert> : null}

      {/* DANH SÁCH cấu hình đã lưu — số thứ tự + mã SÁT TRÁI, công tắc SÁT PHẢI. */}
      {items.length ? (
        <div className={CARD + (open ? " mb-4" : "")}>
          <ul className="m-0 grid list-none gap-2 p-0">
            {items.map((it, i) => {
              const key = it.kind + it.id;
              return (
                <li
                  key={key}
                  className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5"
                >
                  {/* TRÁI: số thứ tự + mã cấu hình (bấm vào để mở bảng soạn) */}
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-200 text-[13px] font-bold text-slate-700">
                    {i + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() => editConfig(it)}
                    className="min-w-0 flex-1 cursor-pointer bg-transparent p-0 text-left"
                    title={t("cf.editThis")}
                  >
                    <span className="block truncate font-mono text-[14px] font-semibold text-slate-800">
                      {it.id}
                    </span>
                    <span className="block truncate text-[12px] text-slate-500">
                      {kindLabel(it.kind)} · {it.display}
                    </span>
                  </button>

                  {/* PHẢI: công tắc áp dụng */}
                  <button
                    type="button"
                    role="switch"
                    aria-checked={it.applied}
                    aria-label={`${t("common.apply")} ${it.id}`}
                    onClick={() => toggleApply(it)}
                    disabled={busyKey === key}
                    className={"ml-auto flex shrink-0 cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 " +
                      (it.applied
                        ? "border-green-200 bg-green-50 text-green-800"
                        : "border-slate-200 bg-white text-slate-600")}
                  >
                    <IconToggle on={it.applied} className="h-6 w-6" />
                    {it.applied ? t("cf.applyOn") : t("common.apply")}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : !open ? (
        <div className={CARD + " text-sm text-slate-500"}>
          {t("cf.noneOwn")}
        </div>
      ) : null}

      {/* BẢNG SOẠN — chỉ hiện khi đang tạo mới hoặc sửa; lưu xong là đóng. */}
      {open ? (
        <div className={CARD}>
          <div className="mb-3 text-sm font-bold text-slate-800">
            {editing ? `${t("cf.editOne")} ${editing}` : t("cf.newTitle")}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="block text-[13px] font-medium text-slate-600">{t("cf.typeLabel")}</span>
              <select
                value={kind}
                disabled={!!editing}
                onChange={(e) => onChangeKind(e.target.value as UserConfigKind)}
                className={FIELD + " mt-1"}
              >
                <option value="jobs">{kindLabel("jobs")}</option>
                <option value="markets">{kindLabel("markets")}</option>
              </select>
              <span className="mt-1 block text-xs text-slate-500">{kindHint(kind)}</span>
            </label>

            <label className="block">
              <span className="block text-[13px] font-medium text-slate-600">
                {t("cf.idLabel")}
              </span>
              <input
                value={id}
                disabled={!!editing}
                onChange={(e) => { setId(e.target.value); setDirty(true); }}
                placeholder={t("cf.idPlaceholder")}
                className={FIELD + " mt-1"}
              />
              <span className="mt-1 block text-xs text-slate-500">
                {t("cf.idNote")}
              </span>
            </label>
          </div>

          <label className="mt-3 block text-[13px] font-medium text-slate-600">{t("cf.content")}</label>
          <textarea
            value={content}
            onChange={(e) => { setContent(e.target.value); setDirty(true); }}
            spellCheck={false}
            className="mt-1 h-[50vh] w-full resize-y rounded-lg border border-slate-200 p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500/30"
          />
          <div className="mt-1 text-xs text-slate-500">
            {t("cf.contentNote")}
          </div>

          {warnings.length ? (
            <Alert kind="warn">
              <b>{t("cf.warnTitle")}</b>
              <ul className="mt-1 mb-0 list-disc pl-5">
                {warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </Alert>
          ) : null}

          {/* VÙNG TẢI FILE CẤU HÌNH — CẢ VÙNG là <label> nên bấm chỗ nào cũng mở hộp
              chọn file, không phải chỉ đúng dòng chữ xanh. */}
          <label
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const f = e.dataTransfer.files?.[0];
              if (f) readConfigFile(f);
            }}
            className={"mt-3 flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors " +
              (dragOver ? "border-blue-500 bg-blue-50" : "border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-blue-50/50")}
          >
            <IconUpload className="h-7 w-7 text-slate-400" />
            <span className="text-sm font-semibold text-blue-600">
              {t("cf.uploadBtn")}
            </span>
            <span className="text-xs text-slate-500">{t("cf.dropHint")}</span>
            <input
              type="file"
              accept=".json,.md,application/json,text/markdown"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) readConfigFile(f); }}
            />
          </label>

          <div className="mt-4 flex flex-wrap items-center justify-end gap-3">
            {dirty ? <span className="mr-auto text-xs text-amber-600">{t("cf.unsaved")}</span> : null}
            {editing ? (
              <button className={BTN} onClick={remove} disabled={saving}>{t("common.delete")}</button>
            ) : null}
            <button className={BTN} onClick={closeEditor} disabled={saving}>{t("common.cancel")}</button>
            <button className={BTN_PRIMARY} onClick={save} disabled={saving || !content.trim() || !id.trim()}>
              {saving ? t("common.saving") : t("cf.saveBtn")}
            </button>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}
