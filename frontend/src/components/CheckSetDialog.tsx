import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  configFieldSets, configRead, configWrite, draftCheckSet, friendly, getRegulationSets, setActiveFieldSet,
} from "../api/client";
import type { CheckSetDraft, CheckType, FieldSetInfo, RegulationSet, ValueType } from "../types";
import { Spinner } from "./Layout";
import {
  IconArrowLeft, IconArrowRight, IconBulb, IconCheck, IconChevronDown, IconChevronRight, IconFile,
  IconInfo, IconNote, IconPlus, IconScale, IconSearch, IconSparkle, IconStack, IconTrash, IconX,
} from "./Icons";
import { BTN, BTN_GHOST, BTN_PRIMARY, FIELD } from "../ui";
import { useT } from "../i18n";
import RegulationSetDialog from "./RegulationSetDialog";
import Tip from "./Tip";

/** Mã an toàn từ chữ: chữ thường, bỏ dấu, chỉ a-z 0-9 _ . */
function slugKey(s: string): string {
  return s
    .replace(/đ/g, "d").replace(/Đ/g, "D")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "")
    .slice(0, 48);
}

type Row = {
  /** Mã trường gốc (bộ được sao chép/đang sửa) — giữ để không đổi mã khi chỉ sửa nhãn. */
  key: string;
  label: string;
  alts: string;
  value_type: ValueType;
  check_type: CheckType;
  check_aspect: string;
  required: boolean;
  in_table: boolean;
  /** Thuộc tính khác của trường gốc (max_len, value_regex…) — giữ nguyên khi lưu. */
  extra: Record<string, unknown>;
};

const EMPTY_ROW: Row = {
  key: "", label: "", alts: "", value_type: "text", check_type: "regulated",
  check_aspect: "", required: true, in_table: false, extra: {},
};

const VALUE_TYPES: ValueType[] = ["text", "date", "number", "money"];
const CHECK_TYPES: CheckType[] = ["regulated", "declaration", "positive_integer"];
const NAME_MAX = 100;
const DESC_MAX = 500;

/** Bộ trường JSON -> các dòng của bảng soạn. */
function rowsOf(fs: Record<string, unknown>): Row[] {
  const fc = (fs.fields_catalog || {}) as Record<string, Record<string, unknown>>;
  const fcm = (fs.field_check_mode || {}) as { always_check?: string[] };
  const always = new Set(fcm.always_check || []);
  return Object.entries(fc).map(([key, e]) => {
    const { label, label_alts, value_type, check_type, check_aspect, in_table, ...extra } = e;
    return {
      key,
      label: String(label || ""),
      alts: Array.isArray(label_alts) ? label_alts.join("; ") : "",
      value_type: (VALUE_TYPES.includes(value_type as ValueType) ? value_type : "text") as ValueType,
      check_type: (CHECK_TYPES.includes(check_type as CheckType) ? check_type : "regulated") as CheckType,
      check_aspect: String(check_aspect || ""),
      required: always.has(key),
      in_table: !!in_table,
      extra,
    };
  });
}

/** Gợi ý từ mô tả bằng lời -> dòng của bảng soạn. Dòng ngày ký mang sẵn mã để chọn làm
 * "ngày ký" mà không phụ thuộc thứ tự dòng. */
function rowsFromDraft(d: CheckSetDraft): Row[] {
  return d.fields.map((f) => ({
    ...EMPTY_ROW,
    key: f.is_signed_date ? slugKey(f.label) : "",
    label: f.label,
    alts: f.label_alts.join("; "),
    value_type: f.value_type,
    check_type: f.check_type,
    check_aspect: f.check_aspect,
    required: f.required,
  }));
}

/** Bốn bước của trình tạo — thứ tự thật người dùng khai báo một bộ kiểm tra. */
const STEPS = ["basic", "regs", "fields", "review"] as const;
type Step = (typeof STEPS)[number];

/** Khối có tiêu đề trong vùng soạn. */
function Panel({ icon, title, desc, aside, children }: {
  icon: ReactNode; title: string; desc?: string; aside?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="rounded-[14px] border border-slate-200 bg-surface">
      <div className="flex items-start gap-3.5 border-b border-slate-200 px-5 py-4">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[11px] border border-blue-200 bg-blue-50 text-blue-700">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="m-0 text-[15.5px] font-extrabold text-slate-800">{title}</h3>
          {desc ? <p className="m-0 mt-0.5 text-[12.5px] text-slate-500">{desc}</p> : null}
        </div>
        {aside}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

/** Nhãn ô nhập: chữ đậm nhỏ, dấu * đỏ khi bắt buộc, bộ đếm ký tự bên phải. */
function FieldLabel({ text, required, count, max }: { text: string; required?: boolean; count?: number; max?: number }) {
  return (
    <span className="mb-1.5 flex items-end justify-between gap-2 text-[12.5px] font-bold text-slate-600">
      <span>
        {text}
        {required ? <b className="text-red-600"> *</b> : null}
      </span>
      {max !== undefined ? <span className="font-mono text-[11px] font-normal text-slate-400">{count ?? 0}/{max}</span> : null}
    </span>
  );
}

/**
 * HỘP THOẠI "BỘ KIỂM TRA" — trình tạo 4 bước (theo mẫu thiết kế).
 *
 * Bộ kiểm tra = các thông tin cần trích xuất từ hồ sơ + tiêu chí kiểm tra từng thông
 * tin + bộ quy định dùng để đối chiếu. Người dùng KHÔNG viết JSON: bảng soạn được đổi
 * sang JSON khi lưu.
 *   · TẠO MỚI: 1 Thông tin cơ bản → 2 Bộ quy định → 3 Thông tin cần kiểm tra → 4 Xem lại
 *     (có thể bắt đầu từ một bộ có sẵn);
 *   · SỬA (`editId`): cùng trình tạo, nạp sẵn bộ của người dùng, lưu đè đúng mã đó;
 *   · DÙNG BỘ CÓ SẴN: chọn bộ kiểm tra đang dùng.
 * Lưu xong, bộ vừa tạo/sửa trở thành bộ kiểm tra đang dùng.
 */
export default function CheckSetDialog({
  activeId,
  onClose,
  onChanged,
  editId = "",
  copyFrom = "",
  initialMode = "new",
}: {
  activeId: string;
  onClose: () => void;
  onChanged: (id: string) => void;
  /** Mã bộ CỦA NGƯỜI DÙNG cần sửa (lưu đè). */
  editId?: string;
  /** Mã bộ dùng làm điểm xuất phát (sao chép thành bộ mới). */
  copyFrom?: string;
  initialMode?: "new" | "pick";
}) {
  const t = useT();
  const [mode, setMode] = useState<"new" | "pick">(editId ? "new" : initialMode);
  const [step, setStep] = useState<Step>("basic");
  const [sets, setSets] = useState<FieldSetInfo[]>([]);
  const [regSets, setRegSets] = useState<RegulationSet[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [regDialog, setRegDialog] = useState(false);
  const [regQuery, setRegQuery] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [triedNext, setTriedNext] = useState(false);

  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState("");
  const [chosenRegs, setChosenRegs] = useState<string[]>([]);
  const [rows, setRows] = useState<Row[]>([{ ...EMPTY_ROW }]);
  const [signedKey, setSignedKey] = useState("");
  const [base, setBase] = useState(copyFrom);
  // Thuộc tính cấp bộ của bộ được sao chép/đang sửa (jurisdiction, doc_types, start_anchor…).
  const [baseExtra, setBaseExtra] = useState<Record<string, unknown>>({});
  // MÔ TẢ BẰNG LỜI: người dùng viết cần kiểm tra gì -> hệ thống điền bảng soạn.
  // Bước XA NHẤT đã tới bằng "Tiếp theo" — tích xanh chỉ cho bước đã hoàn thiện và đã đi qua.
  const [reached, setReached] = useState(editId ? STEPS.length - 1 : 0);
  // Thẻ thông tin đang MỞ để sửa (null = mọi thẻ thu gọn). Một lúc chỉ mở một thẻ.
  const [openRow, setOpenRow] = useState<number | null>(0);
  const [describe, setDescribe] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftNote, setDraftNote] = useState("");

  const loadRegs = () => getRegulationSets().then((d) => setRegSets(d.regulation_sets || [])).catch(() => {});

  useEffect(() => {
    configFieldSets().then((d) => setSets(d.field_sets || [])).catch((e) => setError(friendly(e)));
    loadRegs();
  }, []);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape" && !regDialog && !busy) onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, regDialog, busy]);

  const usable = sets.filter((s) => !s.error && s.fields > 0);

  /** Chọn "Bắt đầu từ bộ có sẵn" ở bước 1: rỗng = bộ trống, có mã = chép bộ đó. */
  function loadFrom(id: string, asCopy: boolean) {
    setBase(asCopy ? id : "");
    if (!id) {
      setRows([{ ...EMPTY_ROW }]);
      setOpenRow(0);
      setBaseExtra({});
      setSignedKey("");
      return;
    }
    fetchInto(id, asCopy);
  }

  /** Đọc bộ `id` từ backend rồi đổ vào bảng soạn. `asCopy` = bắt đầu bộ MỚI từ bộ đó. */
  function fetchInto(id: string, asCopy: boolean) {
    configRead(id)
      .then((d) => {
        const fs = JSON.parse(d.content) as Record<string, unknown>;
        const {
          display_name, description, document_kind, regulation_sets, signed_date_field,
          fields_catalog: _fc, field_check_mode: _fcm, ...rest
        } = fs;
        void _fc; void _fcm;
        const shown = String(display_name || id);
        setName(asCopy ? `${shown} (${t("cs.copy")})`.slice(0, NAME_MAX) : shown);
        setDesc(String(description || ""));
        setKind(String(document_kind || ""));
        setChosenRegs(Array.isArray(regulation_sets) ? (regulation_sets as string[]) : []);
        setSignedKey(String(signed_date_field || ""));
        const r = rowsOf(fs);
        setRows(r.length ? r : [{ ...EMPTY_ROW }]);
        setOpenRow(r.length ? null : 0);
        setBaseExtra(rest);
      })
      .catch((e) => setError(friendly(e)));
  }

  // Mở để SỬA hoặc SAO CHÉP từ trang Bộ kiểm tra -> nạp ngay.
  useEffect(() => {
    if (editId) fetchInto(editId, false);
    else if (copyFrom) fetchInto(copyFrom, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editId, copyFrom]);

  function setRow(i: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  // Mã trường: giữ mã gốc; dòng mới thì suy từ nhãn, trùng thì thêm hậu tố số.
  const keys = useMemo(() => {
    const used = new Set<string>();
    return rows.map((r, i) => {
      let k = r.key || slugKey(r.label) || `truong_${i + 1}`;
      if (/^\d/.test(k)) k = `t_${k}`;
      let out = k;
      for (let n = 2; used.has(out); n++) out = `${k}_${n}`;
      used.add(out);
      return out;
    });
  }, [rows]);

  const dateRows = rows
    .map((r, i) => ({ r, k: keys[i] }))
    .filter(({ r }) => r.value_type === "date" && r.label.trim());
  const filledRows = rows.filter((r) => r.label.trim());
  const requiredCount = filledRows.filter((r) => r.required).length;

  const stepProblem: Record<Step, string> = {
    basic: name.trim() ? "" : t("cs.errName"),
    regs: "",
    fields: filledRows.length ? "" : t("cs.errFields"),
    review: "",
  };
  const problems = STEPS.map((s) => stepProblem[s]).filter(Boolean);
  const stepIdx = STEPS.indexOf(step);

  function goNext() {
    if (stepProblem[step]) {
      setTriedNext(true);
      return;
    }
    setTriedNext(false);
    const next = Math.min(stepIdx + 1, STEPS.length - 1);
    setReached((r) => Math.max(r, next));
    setStep(STEPS[next]);
  }

  /** Mô tả -> gợi ý. Bảng còn trống thì THAY; đã có dòng thì THÊM các thông tin chưa có
   * (so theo nhãn) — không bao giờ xóa thứ người dùng đã soạn. */
  async function runDraft() {
    if (!describe.trim() || drafting) return;
    setDrafting(true);
    setDraftNote("");
    setError("");
    try {
      const d = await draftCheckSet(describe.trim());
      const fresh = rowsFromDraft(d);
      setRows((prev) => {
        const kept = prev.filter((r) => r.label.trim());
        const have = new Set(kept.map((r) => r.label.trim().toLowerCase()));
        const add = fresh.filter((r) => !have.has(r.label.toLowerCase()));
        const next = [...kept, ...add];
        return next.length ? next : [{ ...EMPTY_ROW }];
      });
      if (!kind.trim() && d.document_kind) setKind(d.document_kind);
      if (fresh.length) setOpenRow(null);
      const sd = fresh.find((r) => r.key);
      if (!signedKey && sd) setSignedKey(sd.key);
      setDraftNote(d.note + (d.fields.length ? " " + t("cs.ai.review") : ""));
    } catch (e) {
      setError(friendly(e));
    } finally {
      setDrafting(false);
    }
  }

  async function save() {
    if (problems.length) return setError(problems.join(" "));
    setBusy(true);
    setError("");
    const fc: Record<string, unknown> = {};
    const always: string[] = [];
    rows.forEach((r, i) => {
      if (!r.label.trim()) return;
      const k = keys[i];
      fc[k] = {
        ...r.extra,
        label: r.label.trim(),
        ...(r.alts.trim()
          ? { label_alts: r.alts.split(/[;,\n]/).map((s) => s.trim()).filter(Boolean) }
          : {}),
        value_type: r.value_type,
        check_type: r.check_type,
        check_aspect: r.check_aspect.trim(),
        ...(r.in_table ? { in_table: true } : {}),
      };
      if (r.required) always.push(k);
    });
    const sd = signedKey && fc[signedKey] ? signedKey : "";
    const body = {
      jurisdiction: "VN",
      doc_types: [],
      start_anchor: "",
      ...baseExtra,
      display_name: name.trim(),
      description: desc.trim(),
      document_kind: kind.trim(),
      regulation_sets: chosenRegs,
      ...(sd ? { signed_date_field: sd } : {}),
      fields_catalog: fc,
      field_check_mode: { always_check: always },
    };
    let id = editId;
    if (!id) {
      id = slugKey(name) || "bo_kiem_tra";
      const taken = new Set(sets.map((s) => s.id));
      for (let n = 2; taken.has(id); n++) id = `${slugKey(name) || "bo_kiem_tra"}_${n}`;
    }
    try {
      const r = await configWrite(id, JSON.stringify(body, null, 2));
      await setActiveFieldSet(r.id);
      onChanged(r.id);
      onClose();
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  }

  async function pick(id: string) {
    setBusy(true);
    try {
      await setActiveFieldSet(id);
      onChanged(id);
      onClose();
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  }

  const shownRegs = regSets.filter((r) => !regQuery.trim() ||
    (r.name + " " + r.titles.join(" ")).toLowerCase().includes(regQuery.trim().toLowerCase()));
  const showErr = (s: Step) => triedNext && step === s && stepProblem[s];

  const stepMeta: Record<Step, { title: string; desc: string }> = {
    basic: { title: t("cs.s.basic"), desc: t("cs.s.basicDesc") },
    regs: { title: t("cs.s.regs"), desc: t("cs.s.regsDesc") },
    fields: { title: t("cs.s.fields"), desc: t("cs.s.fieldsDesc") },
    review: { title: t("cs.s.review"), desc: t("cs.s.reviewDesc") },
  };

  const heading = editId ? t("cs.editTitle") : t("cs.title");

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={heading}>
      <div className="rise-in flex h-[min(860px,92vh)] w-[min(1240px,96vw)] flex-col overflow-hidden rounded-[18px] border border-slate-300 bg-[var(--c-bg)] shadow-2xl">
        {/* ĐẦU HỘP THOẠI */}
        <div className="flex items-center gap-4 border-b border-slate-200 px-6 py-4">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-blue-200 bg-blue-50 text-blue-700">
            <IconNote className="h-6 w-6" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[19px] font-extrabold tracking-[-0.01em] text-slate-800">{heading}</div>
            <div className="text-[13px] text-slate-500">{t("cs.subtitle")}</div>
          </div>
          {!editId ? (
            <div className="flex rounded-[11px] border border-slate-200 bg-slate-50 p-1" role="tablist">
              {(["new", "pick"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="tab"
                  aria-selected={mode === m}
                  onClick={() => setMode(m)}
                  className={"cursor-pointer rounded-lg border-0 px-3.5 py-1.5 text-[13px] font-bold transition-colors " +
                    (mode === m ? "bg-blue-600 text-white" : "bg-transparent text-slate-500 hover:text-slate-800")}
                >
                  {m === "new" ? t("cs.tabNew") : t("cs.tabPick")}
                </button>
              ))}
            </div>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="grid h-9 w-9 cursor-pointer place-items-center rounded-lg border-0 bg-transparent text-slate-400 hover:bg-slate-100 hover:text-slate-800"
            title={t("common.close")}
            aria-label={t("common.close")}
          >
            <IconX className="h-5 w-5" />
          </button>
        </div>

        {mode === "pick" ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {error ? <div className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">{error}</div> : null}
            {usable.length ? (
              <ul className="m-0 grid list-none gap-3 p-0 md:grid-cols-2">
                {usable.map((s) => {
                  const on = s.id === activeId;
                  return (
                    <li key={s.id} className={"flex items-start gap-3.5 rounded-[14px] border p-4 " +
                      (on ? "border-blue-500 bg-blue-50" : "border-slate-200 bg-surface")}>
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[11px] bg-slate-100 text-slate-500">
                        <IconStack className="h-5 w-5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[14.5px] font-bold text-slate-800">{s.display_name}</div>
                        <div className="mt-0.5 text-[12.5px] text-slate-500">
                          {s.fields} {t("up.fieldCount")}
                          {s.document_kind ? ` · ${s.document_kind}` : ""}
                        </div>
                        <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                          {t("cs.regs")}: {s.regulation_sets?.length ? s.regulation_sets.join(", ") : t("cs.allRegs")}
                        </div>
                      </div>
                      {on ? (
                        <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[12px] font-bold text-blue-700">{t("cs.inUse")}</span>
                      ) : (
                        <button type="button" className={BTN + " !px-3 !py-1.5 text-xs"} disabled={busy} onClick={() => pick(s.id)}>
                          {t("cs.use")}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="m-0 text-center text-sm text-slate-500">{t("cs.noneYet")}</p>
            )}
          </div>
        ) : (
          <div className={"grid min-h-0 flex-1 grid-cols-[252px_minmax(0,1fr)] " +
            // Bước "Thông tin cần kiểm tra" cần cả bề ngang cho bảng soạn -> ẩn cột tóm tắt.
            (step === "fields" ? "" : "xl:grid-cols-[252px_minmax(0,1fr)_280px]")}>
            {/* CỘT BƯỚC */}
            <nav className="flex flex-col gap-1 overflow-y-auto border-r border-slate-200 p-4" aria-label={t("cs.stepsAria")}>
              {STEPS.map((s, i) => {
                const on = s === step;
                // Chỉ bấm được bước ĐANG ĐỨNG và các bước PHÍA TRƯỚC; bước sau phải đi bằng
                // "Tiếp theo" để từng bước được kiểm hợp lệ. Sửa bộ có sẵn thì mọi bước đã đủ.
                const canGo = editId ? i <= reached : i <= stepIdx;
                // Tích xanh: bước đã ĐI QUA và đang hợp lệ (bước Xem lại chỉ xong khi lưu).
                const done = !on && i < reached && i < STEPS.length - 1 && !stepProblem[s];
                return (
                  <Fragment key={s}>
                    <button
                      type="button"
                      onClick={() => canGo && setStep(s)}
                      disabled={!canGo}
                      aria-current={on ? "step" : undefined}
                      title={canGo ? undefined : t("cs.stepLocked")}
                      className={"flex items-start gap-3 rounded-xl border-0 px-3 py-3 text-left transition-colors " +
                        (on ? "bg-blue-50" : canGo ? "cursor-pointer bg-transparent hover:bg-slate-50" : "cursor-not-allowed bg-transparent opacity-55")}
                    >
                      <span className={"step-dot mt-0.5 " + (on ? "is-current" : done ? "is-done" : "")}>
                        {done ? <IconCheck className="h-4 w-4" /> : i + 1}
                      </span>
                      <span className="min-w-0">
                        <span className={"block text-[14px] font-bold " + (on ? "text-blue-700" : "text-slate-700")}>{stepMeta[s].title}</span>
                        <span className="block text-[12px] leading-snug text-slate-500">{stepMeta[s].desc}</span>
                      </span>
                    </button>
                    {i < STEPS.length - 1 ? <span aria-hidden="true" className="ml-[24px] h-3 w-px bg-slate-300" /> : null}
                  </Fragment>
                );
              })}
              <Tip id="cs.tip" className="mt-auto rounded-xl border border-slate-200 bg-surface p-3.5">
                <div className="flex items-center gap-2 text-[13px] font-bold text-amber-600">
                  <IconBulb className="h-4.5 w-4.5" /> {t("cs.tipTitle")}
                </div>
                <p className="m-0 mt-1.5 text-[12px] leading-relaxed text-slate-500">{t(`cs.tip.${step}`)}</p>
              </Tip>
            </nav>

            {/* VÙNG SOẠN */}
            <div className="min-h-0 overflow-y-auto p-6">
              {error ? <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">{error}</div> : null}

              {step === "basic" ? (
                <div className="rise-in grid grid-cols-[minmax(0,1fr)] gap-5">
                  <Panel icon={<IconFile className="h-5 w-5" />} title={`1. ${t("cs.s.basic")}`} desc={t("cs.s.basicLong")}>
                    <div className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
                      <label className="block">
                        <FieldLabel text={t("cs.name")} required count={name.length} max={NAME_MAX} />
                        <input
                          className={FIELD + " h-11" + (showErr("basic") ? " !border-red-400" : "")}
                          value={name}
                          maxLength={NAME_MAX}
                          autoFocus
                          onChange={(e) => setName(e.target.value)}
                          placeholder={t("cs.namePh")}
                        />
                        {showErr("basic") ? <span className="mt-1 block text-[12px] font-semibold text-red-600">{stepProblem.basic}</span> : null}
                      </label>
                      <label className="block">
                        <FieldLabel text={t("cs.kind")} />
                        <input className={FIELD + " h-11"} value={kind} list="cs-kinds" onChange={(e) => setKind(e.target.value)} placeholder={t("cs.kindPh")} />
                        <datalist id="cs-kinds">
                          {Array.from(new Set(sets.map((s) => s.document_kind).filter(Boolean))).map((k) => <option key={k} value={k} />)}
                        </datalist>
                      </label>
                    </div>
                    <label className="mt-4 block">
                      <FieldLabel text={t("cs.desc")} count={desc.length} max={DESC_MAX} />
                      <textarea
                        className={FIELD + " min-h-[92px] resize-y"}
                        value={desc}
                        maxLength={DESC_MAX}
                        onChange={(e) => setDesc(e.target.value)}
                        placeholder={t("cs.descPh")}
                      />
                    </label>
                  </Panel>

                  {!editId ? (
                    <Panel icon={<IconStack className="h-5 w-5" />} title={t("cs.base")} desc={t("cs.baseDesc")}>
                      <select className={FIELD + " h-11 cursor-pointer"} value={base} onChange={(e) => loadFrom(e.target.value, true)}>
                        <option value="">{t("cs.baseNone")}</option>
                        {usable.map((s) => <option key={s.id} value={s.id}>{s.display_name} — {s.fields} {t("up.fieldCount")}</option>)}
                      </select>
                    </Panel>
                  ) : null}
                </div>
              ) : null}

              {step === "regs" ? (
                <div className="rise-in">
                  <Panel
                    icon={<IconScale className="h-5 w-5" />}
                    title={`2. ${t("cs.s.regs")}`}
                    desc={t("cs.s.regsLong")}
                    aside={
                      <button type="button" className={BTN_PRIMARY + " !px-3.5"} onClick={() => setRegDialog(true)}>
                        <IconPlus className="h-4.5 w-4.5" /> {t("up.addRegSet")}
                      </button>
                    }
                  >
                    <div className="mb-3 flex flex-wrap items-center gap-3">
                      <label className="relative min-w-[220px] flex-1">
                        <IconSearch className="pointer-events-none absolute left-3 top-1/2 h-4.5 w-4.5 -translate-y-1/2 text-slate-400" />
                        <input className={FIELD + " h-10 pl-9"} value={regQuery} onChange={(e) => setRegQuery(e.target.value)} placeholder={t("cs.regSearch")} aria-label={t("cs.regSearch")} />
                      </label>
                      <span className={"rounded-full px-3 py-1 text-[12.5px] font-bold " + (chosenRegs.length ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500")}>
                        {chosenRegs.length ? `${t("cs.regPicked")}: ${chosenRegs.length}` : t("cs.regAllNote")}
                      </span>
                      {chosenRegs.length ? (
                        <button type="button" className="cursor-pointer border-0 bg-transparent p-0 text-[12.5px] font-bold text-blue-700 hover:underline" onClick={() => setChosenRegs([])}>
                          {t("cs.regClear")}
                        </button>
                      ) : null}
                    </div>
                    {regSets.length ? (
                      <ul className="m-0 flex list-none flex-col overflow-hidden rounded-xl border border-slate-200 p-0">
                        {shownRegs.map((r) => {
                          const on = chosenRegs.includes(r.name);
                          return (
                            <li key={r.name} className="border-t border-slate-200 first:border-t-0">
                              <label className={"flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors " + (on ? "bg-blue-50" : "hover:bg-slate-50")}>
                                <input
                                  type="checkbox"
                                  className="mt-1 h-4 w-4"
                                  checked={on}
                                  onChange={(e) => setChosenRegs((prev) => e.target.checked ? [...prev, r.name] : prev.filter((x) => x !== r.name))}
                                />
                                <span className="min-w-0 flex-1">
                                  <span className="block text-[14px] font-bold text-slate-800">{r.name}</span>
                                  <span className="block truncate text-[12.5px] text-slate-500">
                                    {r.titles.length ? r.titles.slice(0, 3).join(" · ") + (r.titles.length > 3 ? " …" : "") : r.files.join(", ")}
                                  </span>
                                </span>
                                <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-0.5 text-[12px] font-bold text-slate-600">
                                  {r.documents} {t("rset.docs")}
                                </span>
                              </label>
                            </li>
                          );
                        })}
                        {!shownRegs.length ? <li className="px-4 py-5 text-center text-[13px] text-slate-500">{t("cs.regNoMatch")}</li> : null}
                      </ul>
                    ) : (
                      <div className="rounded-xl border border-dashed border-slate-300 px-5 py-6 text-center text-[13px] text-slate-500">
                        {t("cs.regEmpty")}
                      </div>
                    )}
                    <Tip id="cs.regsHint" className="mt-3 rounded-lg bg-slate-50 py-2 pl-3">
                      <p className="m-0 flex items-start gap-2 text-[12.5px] text-slate-500">
                        <IconInfo className="h-4.5 w-4.5 shrink-0 text-blue-700" /> {t("cs.regsHint")}
                      </p>
                    </Tip>
                  </Panel>
                </div>
              ) : null}

              {step === "fields" ? (
                <div className="rise-in grid grid-cols-[minmax(0,1fr)] gap-4">
                  <Panel icon={<IconSparkle className="h-5 w-5" />} title={t("cs.ai.title")} desc={t("cs.ai.desc")}>
                    <textarea
                      className={FIELD + " min-h-[120px] resize-y leading-relaxed"}
                      value={describe}
                      maxLength={6000}
                      disabled={drafting}
                      onChange={(e) => setDescribe(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runDraft(); }}
                      placeholder={t("cs.ai.ph")}
                      aria-label={t("cs.ai.title")}
                    />
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <button type="button" className={BTN_PRIMARY} disabled={drafting || !describe.trim()} onClick={runDraft}>
                        {drafting ? <><Spinner /> {t("cs.ai.running")}</> : <><IconSparkle className="h-4.5 w-4.5" /> {t("cs.ai.run")}</>}
                      </button>
                      <span className="min-w-0 flex-1 text-[12.5px] text-slate-500" aria-live="polite">
                        {drafting ? t("cs.ai.wait") : draftNote || t("cs.ai.hint")}
                      </span>
                    </div>
                  </Panel>

                  <Panel
                    icon={<IconCheck className="h-5 w-5" />}
                    title={`3. ${t("cs.s.fields")}`}
                    desc={t("cs.s.fieldsLong")}
                    aside={<span className="rounded-full bg-slate-100 px-3 py-1 text-[12.5px] font-bold text-slate-600">{filledRows.length} {t("cs.fieldUnit")}</span>}
                  >
                    {showErr("fields") ? <div className="mb-3 text-[12.5px] font-semibold text-red-600">{stepProblem.fields}</div> : null}
                    {/* MỖI THÔNG TIN MỘT THẺ. Thu gọn: chỉ nhãn + "Bắt buộc"/"Trong bảng" (xem, không
                        sửa). Bấm thẻ để mở rộng và sửa; mỗi lúc mở một thẻ. Ô nhãn và tên gọi khác
                        mỗi ô một hàng, đủ rộng để đọc hết. */}
                    <ul className="m-0 grid list-none gap-2 p-0">
                      {rows.map((r, i) => {
                        const open = openRow === i;
                        return (
                          <li key={i} className={"rounded-xl border bg-surface " + (open ? "border-blue-300 shadow-sm" : "border-slate-200")}>
                            <div className="flex items-center gap-3 px-3.5 py-2.5">
                              <button type="button" onClick={() => setOpenRow(open ? null : i)} aria-expanded={open}
                                className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 border-0 bg-transparent p-0 text-left">
                                {open ? <IconChevronDown className="h-4.5 w-4.5 shrink-0 text-slate-400" /> : <IconChevronRight className="h-4.5 w-4.5 shrink-0 text-slate-400" />}
                                <span className={"min-w-0 truncate text-[14px] font-bold " + (r.label.trim() ? "text-slate-800" : "text-slate-400")}>
                                  {r.label.trim() || t("cs.cardNoLabel")}
                                </span>
                                {!open ? (
                                  <span className="shrink-0 rounded-md bg-slate-100 px-2 py-0.5 text-[11.5px] font-semibold text-slate-500">{t(`cs.vt.${r.value_type}`)}</span>
                                ) : null}
                              </button>
                              <label className="flex shrink-0 items-center gap-1.5 text-[12.5px] font-semibold text-slate-600">
                                <input type="checkbox" className="h-4 w-4" checked={r.required} disabled={!open}
                                  onChange={(e) => setRow(i, { required: e.target.checked })} />
                                {t("cs.colRequired")}
                              </label>
                              <label className="flex shrink-0 items-center gap-1.5 text-[12.5px] font-semibold text-slate-600" title={t("cs.colTableHint")}>
                                <input type="checkbox" className="h-4 w-4" checked={r.in_table} disabled={!open}
                                  onChange={(e) => setRow(i, { in_table: e.target.checked })} />
                                {t("cs.colTable")}
                              </label>
                              {busy || drafting ? null : (
                                <button type="button" title={t("cs.removeRow")} aria-label={t("cs.removeRow")}
                                  className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-lg border-0 bg-transparent text-slate-400 hover:bg-red-50 hover:text-red-600"
                                  onClick={() => {
                                    setRows((prev) => prev.length > 1 ? prev.filter((_, j) => j !== i) : [{ ...EMPTY_ROW }]);
                                    setOpenRow((o) => (o === i ? null : o !== null && o > i ? o - 1 : o));
                                  }}>
                                  <IconTrash className="h-4.5 w-4.5" />
                                </button>
                              )}
                            </div>
                            {open ? (
                              <div className="grid gap-3 border-t border-slate-200 px-3.5 py-3.5">
                                <label className="block">
                                  <FieldLabel text={t("cs.colLabel")} required />
                                  <input className={FIELD + " h-10"} value={r.label} autoFocus
                                    onChange={(e) => setRow(i, { label: e.target.value })} placeholder={t("cs.labelPh")} />
                                </label>
                                <label className="block">
                                  <FieldLabel text={t("cs.colAlts")} />
                                  <input className={FIELD + " h-10"} value={r.alts}
                                    onChange={(e) => setRow(i, { alts: e.target.value })} placeholder={t("cs.altsPh")} />
                                </label>
                                <div className="grid gap-3 md:grid-cols-2">
                                  <label className="block">
                                    <FieldLabel text={t("cs.colType")} />
                                    <select className={FIELD + " h-10 cursor-pointer"} value={r.value_type}
                                      onChange={(e) => setRow(i, { value_type: e.target.value as ValueType })}>
                                      {VALUE_TYPES.map((v) => <option key={v} value={v}>{t(`cs.vt.${v}`)}</option>)}
                                    </select>
                                  </label>
                                  <label className="block">
                                    <FieldLabel text={t("cs.colCheck")} />
                                    <select className={FIELD + " h-10 cursor-pointer"} value={r.check_type}
                                      onChange={(e) => setRow(i, { check_type: e.target.value as CheckType })}>
                                      {CHECK_TYPES.map((v) => <option key={v} value={v}>{t(`cs.ct.${v}`)}</option>)}
                                    </select>
                                  </label>
                                </div>
                                <label className="block">
                                  <FieldLabel text={t("cs.colCriteria")} />
                                  <textarea rows={2} className={FIELD + " block min-h-[64px] resize-y leading-snug"} value={r.check_aspect}
                                    onChange={(e) => setRow(i, { check_aspect: e.target.value })} placeholder={t("cs.criteriaPh")} />
                                </label>
                              </div>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <button type="button" className={BTN + " !border-blue-300 !text-blue-700"} onClick={() => { setOpenRow(rows.length); setRows((prev) => [...prev, { ...EMPTY_ROW }]); }}>
                        <IconPlus className="h-4.5 w-4.5" /> {t("cs.addRow")}
                      </button>
                      <span className="text-[12.5px] text-slate-500">{t("cs.addRowHint")}</span>
                    </div>
                  </Panel>

                  <div className="rounded-[14px] border border-slate-200 bg-surface">
                    <button
                      type="button"
                      onClick={() => setMoreOpen((v) => !v)}
                      aria-expanded={moreOpen}
                      className="flex w-full cursor-pointer items-center gap-2.5 border-0 bg-transparent px-5 py-3.5 text-left text-[14px] font-bold text-slate-700"
                    >
                      {moreOpen ? <IconChevronDown className="h-5 w-5" /> : <IconChevronRight className="h-5 w-5" />}
                      {t("cs.more")}
                    </button>
                    {moreOpen ? (
                      <div className="border-t border-slate-200 px-5 py-4">
                        <label className="block max-w-md">
                          <FieldLabel text={t("cs.signed")} />
                          <select className={FIELD + " h-10 cursor-pointer"} value={signedKey} onChange={(e) => setSignedKey(e.target.value)}>
                            <option value="">{t("cs.signedNone")}</option>
                            {dateRows.map(({ r, k }) => <option key={k} value={k}>{r.label}</option>)}
                          </select>
                        </label>
                        <p className="m-0 mt-3 text-[12.5px] leading-relaxed text-slate-500">{t("cs.help")}</p>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {step === "review" ? (
                <div className="rise-in grid gap-4">
                  <Panel icon={<IconCheck className="h-5 w-5" />} title={`4. ${t("cs.s.review")}`} desc={t("cs.s.reviewLong")}>
                    {problems.length ? (
                      <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
                        <b>{t("cs.reviewMissing")}</b>
                        <ul className="m-0 mt-1 list-disc pl-5">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
                      </div>
                    ) : null}
                    <dl className="m-0 grid gap-x-6 gap-y-3 text-[13.5px] md:grid-cols-[180px_minmax(0,1fr)]">
                      <dt className="font-bold text-slate-500">{t("cs.name")}</dt>
                      <dd className="m-0 font-semibold text-slate-800">{name || "—"}</dd>
                      <dt className="font-bold text-slate-500">{t("cs.kind")}</dt>
                      <dd className="m-0 text-slate-800">{kind || "—"}</dd>
                      <dt className="font-bold text-slate-500">{t("cs.desc")}</dt>
                      <dd className="m-0 text-slate-800">{desc || "—"}</dd>
                      <dt className="font-bold text-slate-500">{t("cs.regs")}</dt>
                      <dd className="m-0 text-slate-800">{chosenRegs.length ? chosenRegs.join(", ") : t("up.regsAll")}</dd>
                    </dl>
                  </Panel>
                  <div className="overflow-hidden rounded-[14px] border border-slate-200 bg-surface">
                    <div className="border-b border-slate-200 px-5 py-3 text-[14px] font-bold text-slate-800">
                      {t("cs.s.fields")} · {filledRows.length} ({requiredCount} {t("cs.requiredUnit")})
                    </div>
                    <ul className="m-0 list-none p-0">
                      {filledRows.map((r, i) => (
                        <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-200 px-5 py-2.5 text-[13px] first:border-t-0">
                          <span className="min-w-[180px] font-semibold text-slate-800">{r.label}</span>
                          <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[12px] font-semibold text-slate-600">{t(`cs.vt.${r.value_type}`)}</span>
                          <span className="rounded-md bg-blue-50 px-2 py-0.5 text-[12px] font-semibold text-blue-700">{t(`cs.ct.${r.check_type}`)}</span>
                          {r.required ? <span className="rounded-md bg-amber-50 px-2 py-0.5 text-[12px] font-semibold text-amber-700">{t("cs.colRequired")}</span> : null}
                          {r.check_aspect ? <span className="min-w-0 flex-1 truncate text-slate-500">{r.check_aspect}</span> : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}
            </div>

            {/* TÓM TẮT — cập nhật theo từng ô nhập */}
            <aside className={"hidden overflow-y-auto border-l border-slate-200 p-5 " + (step === "fields" ? "" : "xl:block")}>
              <div className="mb-4 flex items-center gap-2.5 text-[15px] font-extrabold text-slate-800">
                <IconNote className="h-5 w-5 text-blue-700" /> {t("cs.summary")}
              </div>
              <div className="grid gap-3.5 text-[13px]">
                <div>
                  <div className="text-[12px] font-bold text-slate-500">{t("cs.name")}</div>
                  <div className={"mt-0.5 font-semibold " + (name ? "text-slate-800" : "text-slate-400")}>{name || t("cs.notYet")}</div>
                </div>
                <div>
                  <div className="text-[12px] font-bold text-slate-500">{t("cs.kind")}</div>
                  <div className={"mt-0.5 " + (kind ? "text-slate-800" : "text-slate-400")}>{kind || t("cs.notYet")}</div>
                </div>
                <div>
                  <div className="text-[12px] font-bold text-slate-500">{t("cs.desc")}</div>
                  <div className={"mt-0.5 line-clamp-3 " + (desc ? "text-slate-800" : "text-slate-400")}>{desc || t("cs.notYet")}</div>
                </div>
                <button type="button" onClick={() => setStep("regs")} disabled={!(editId || stepIdx >= 1)}
                  className="flex items-center gap-3 rounded-xl border border-slate-200 bg-surface p-3 text-left enabled:cursor-pointer enabled:hover:border-slate-300 disabled:cursor-default">
                  <IconScale className="h-5 w-5 shrink-0 text-blue-700" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[12px] font-bold text-slate-500">{t("cs.regs")}</span>
                    <span className="block truncate font-bold text-slate-800">
                      {chosenRegs.length ? `${chosenRegs.length} ${t("up.regsSetUnit")}` : t("up.regsAll")}
                    </span>
                  </span>
                  <IconChevronRight className="h-4.5 w-4.5 text-slate-400" />
                </button>
                <button type="button" onClick={() => setStep("fields")} disabled={!(editId || stepIdx >= 2)}
                  className="flex items-center gap-3 rounded-xl border border-slate-200 bg-surface p-3 text-left enabled:cursor-pointer enabled:hover:border-slate-300 disabled:cursor-default">
                  {filledRows.length
                    ? <IconCheck className="h-5 w-5 shrink-0 text-green-600" />
                    : <IconNote className="h-5 w-5 shrink-0 text-slate-400" />}
                  <span className="min-w-0 flex-1">
                    <span className="block text-[12px] font-bold text-slate-500">{t("cs.s.fields")}</span>
                    <span className="block font-bold text-slate-800">
                      {filledRows.length} {t("cs.fieldUnit")} · {requiredCount} {t("cs.requiredUnit")}
                    </span>
                  </span>
                  <IconChevronRight className="h-4.5 w-4.5 text-slate-400" />
                </button>
              </div>
            </aside>
          </div>
        )}

        {/* CHÂN HỘP THOẠI */}
        {mode === "new" ? (
          <div className="flex items-center gap-3 border-t border-slate-200 px-6 py-3.5">
            <span className="flex-1 text-[12.5px] text-slate-500">
              {t("cs.stepOf").replace("{i}", String(stepIdx + 1)).replace("{n}", String(STEPS.length))}
            </span>
            <button type="button" className={BTN_GHOST} onClick={onClose} disabled={busy}>{t("cs.cancel")}</button>
            {stepIdx > 0 ? (
              <button type="button" className={BTN} onClick={() => setStep(STEPS[stepIdx - 1])} disabled={busy}>
                <IconArrowLeft className="h-4.5 w-4.5" /> {t("cs.back")}
              </button>
            ) : null}
            {step !== "review" ? (
              <button type="button" className={BTN_PRIMARY + " px-6"} onClick={goNext}>
                {t("cs.next")} <IconArrowRight className="h-4.5 w-4.5" />
              </button>
            ) : (
              <button type="button" className={BTN_PRIMARY + " px-6"} disabled={busy || problems.length > 0} onClick={save}>
                {busy ? <><Spinner /> {t("common.saving")}</> : editId ? t("cs.saveEdit") : t("cs.save")}
              </button>
            )}
          </div>
        ) : null}
      </div>

      {regDialog ? (
        <RegulationSetDialog
          onClose={() => setRegDialog(false)}
          onAdded={(setName) => {
            loadRegs();
            setChosenRegs((prev) => (prev.includes(setName) ? prev : [...prev, setName]));
          }}
        />
      ) : null}
    </div>
  );
}
