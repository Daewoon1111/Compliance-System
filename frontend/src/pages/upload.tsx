import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router-dom";
import {
  classifyFiles,
  friendly,
  getMarkets,
  getOcrDpi,
  setOcrDpi,
} from "../api/client";
import {
  clearUploadResult,
  getUploadJob,
  startUploadJob,
  subscribeUploadJob,
} from "../progressStore";
import type { Market, Region } from "../types";
import { AppShell, Alert, Spinner } from "../components/Layout";
import { notify } from "../notify";
import { setLastCheckPath } from "../session";
import { CARD, BTN_PRIMARY, FIELD } from "../ui";
import { IconUpload, IconX } from "../components/Icons";
import { translate, useCatLabel, useLang, useT } from "../i18n";
import SearchSelect from "../components/SearchSelect";

const KHAC = "khac";

// HẠN MỨC — phải khớp MAX_FILES / MAX_FILE_BYTES / MAX_TOTAL_BYTES ở
// backend/app/routers/sessions.py. Chặn tại đây để người dùng biết ngay thay vì
// chờ upload xong mới nhận 413.
const MAX_FILES = 20;
const MAX_FILE_MB = 50;
const MAX_TOTAL_MB = 150;

// Lưu nháp ở cấp module -> điều hướng qua lại (vd "Trở lại" ở trang 2) vẫn giữ lựa chọn.
const draft = {
  files: [] as File[],
  region: "", market: "", country: "", jobType: "", marketOther: "", jobTypeOther: "",
};

/** Ô chọn 1 dòng: nhãn + <select> + tùy chọn "Khác". Gom 3 ô Khu vực / Quốc gia /
 *  Loại hình lao động vốn viết lặp y hệt nhau. */
function Pick(props: {
  label: string;
  placeholder: string;
  searchPlaceholder: string;
  emptyText: string;
  otherLabel: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  options: { value: string; label: string }[];
  withOther?: boolean;
}) {
  // Danh mục nay mang cả tên tiếng Anh lẫn tiếng Việt nên dài hơn hẳn -> dùng ô chọn
  // CÓ TÌM KIẾM (gõ được cả hai thứ tiếng, không cần bỏ dấu) thay cho <select> gốc.
  const opts = props.withOther
    ? [...props.options, { value: KHAC, label: props.otherLabel }]
    : props.options;
  return (
    <div className="block">
      <div className="mb-1.5 text-sm font-semibold">{props.label}</div>
      <SearchSelect
        value={props.value}
        onChange={props.onChange}
        options={opts}
        placeholder={props.placeholder}
        searchPlaceholder={props.searchPlaceholder}
        emptyText={props.emptyText}
        disabled={props.disabled}
      />
    </div>
  );
}

/** Ô nhập tự do khi chọn "Khác" — dùng chung cho thị trường và loại hình lao động. */
function OtherInput(props: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  invalid: boolean;
  disabled?: boolean;
  badText: string;
}) {
  const bad = props.invalid && !!props.value;
  return (
    <label className="block">
      <input
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        disabled={props.disabled}
        placeholder={props.placeholder}
        className={FIELD + (bad ? " border-red-400" : "")}
      />
      {bad ? (
        <div className="mt-1 text-xs text-red-600">{props.badText}</div>
      ) : null}
    </label>
  );
}

/** Kiểm soát ô nhập "khác" (đồng bộ với backend is_meaningful_text): đủ dài, có chữ,
 * ≥2 từ, không phải 1 ký tự lặp lại. Tránh nhập bừa khiến LLM không hiểu. */
function meaningful(s: string): boolean {
  const t = (s || "").trim();
  if (t.length < 6) return false;
  if (!/[A-Za-zÀ-ỹ]/.test(t)) return false;
  const words = t.split(/\s+/).filter((w) => /[A-Za-zÀ-ỹ0-9]/.test(w));
  if (words.length < 2) return false;
  if (new Set(t.replace(/\s+/g, "")).size <= 2) return false;
  return true;
}

export default function Upload() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [regionId, setRegionId] = useState(draft.region);
  const [marketId, setMarketId] = useState(draft.market);
  const [countryId, setCountryId] = useState(draft.country);
  const [jobTypeId, setJobTypeId] = useState(draft.jobType);
  const [marketOther, setMarketOther] = useState(draft.marketOther);
  const [jobTypeOther, setJobTypeOther] = useState(draft.jobTypeOther);
  const [files, setFiles] = useState<File[]>(draft.files);
  const [roleMap, setRoleMap] = useState<Record<string, string>>({});
  const [dragOver, setDragOver] = useState(false);
  // Vùng chọn file ẨN sau khi đã có file; bấm "Thay đổi" để hiện lại (thêm/đổi file).
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dpi, setDpi] = useState<number>(200);
  const [dpiBounds, setDpiBounds] = useState<{ min: number; max: number }>({
    min: 120,
    max: 300,
  });
  // BẬC do DPI kéo theo (số ô ảnh Vintern + số đoạn luật). Backend là nguồn duy nhất —
  // dựng lại bảng bậc ở frontend nghĩa là hai bản sao rồi sẽ lệch nhau.
  const [dpiTier, setDpiTier] = useState<{ vi: string; en: string }>({ vi: "", en: "" });
  const [dpiSaving, setDpiSaving] = useState(false);
  // Tiến trình OCR nằm ở KHO TOÀN CỤC (progressStore) -> chuyển trang không mất;
  // quay lại trang này vẫn thấy tiến độ, xong việc tự chuyển sang bước kiểm tra.
  const job = useSyncExternalStore(subscribeUploadJob, getUploadJob);
  const loading = job.active;
  const progress = job.text;
  const eta = job.eta;
  const [pageError, setPageError] = useState("");
  // Lỗi hiển thị = lỗi của TRANG (thiếu lựa chọn, tải danh mục hỏng) HOẶC lỗi job từ kho.
  // Đọc thẳng khi render -> không mirror sang state trong effect (cascading render).
  const error = pageError || job.error;
  const nav = useNavigate();
  const t = useT();
  const cat = useCatLabel();
  const lang = useLang();

  // Xong (kể cả khi xong lúc đang ở trang khác) -> chuyển sang bước kiểm tra.
  useEffect(() => {
    if (!job.sessionId) return;
    const sid = job.sessionId;
    clearUploadResult();
    // Thông báo hoàn tất do KHO phát (hiện ở mọi trang); ở đây chỉ lo chuyển bước.
    nav(`/review/${sid}`);
  }, [job.sessionId, nav]);

  // Đang ở bước TẢI LÊN -> "Kiểm tra" trên thanh nav quay về đây.
  useEffect(() => {
    setLastCheckPath("/kiem-tra");
  }, []);

  // MỘT effect ghi nháp cho cả 7 lựa chọn: cùng một việc thì cùng một nhịp chạy.
  useEffect(() => {
    Object.assign(draft, {
      files, region: regionId, market: marketId, country: countryId,
      jobType: jobTypeId, marketOther, jobTypeOther,
    });
  }, [files, regionId, marketId, countryId, jobTypeId, marketOther, jobTypeOther]);

  // Nhãn vai trò lấy theo NGÔN NGỮ đang chọn. `lang` nằm trong deps để đổi ngôn ngữ
  // là nhãn đổi theo — backend trả kèm cả hai bản nên đây chỉ là chọn lại, không phải
  // một lượt gọi API mới (request giống hệt đã được gộp ở tầng `api`).
  useEffect(() => {
    if (!files.length) return;
    classifyFiles(files.map((f) => f.name))
      .then((r) => {
        const m: Record<string, string> = {};
        for (const it of r.roles || []) {
          m[it.filename] = (lang === "en" ? it.label_en : it.label) || it.label;
        }
        setRoleMap(m);
      })
      .catch(() => setRoleMap({}));
  }, [files, lang]);
  // View lọc theo file hiện tại -> khi xóa hết file, nhãn tự rỗng (không cần setState).
  const roleMapView = useMemo(() => {
    const names = new Set(files.map((f) => f.name));
    const o: Record<string, string> = {};
    for (const [k, v] of Object.entries(roleMap)) if (names.has(k)) o[k] = v;
    return o;
  }, [files, roleMap]);
  useEffect(() => {
    getMarkets()
      .then((cfg) => {
        setMarkets(cfg.markets || []);
        // Khu vực CHỈ hiện những khu vực thực sự có quốc gia trong cấu hình
        // (hoặc thị trường do người dùng tạo gắn thẳng vào khu vực, không có quốc gia).
        const used = new Set(
          (cfg.markets || []).flatMap((m) => [
            m.region_id || "",
            ...(m.countries || []).map((c) => c.region_id || ""),
          ]),
        );
        setRegions((cfg.regions || []).filter((r) => used.has(r.id)));
        // KHÔNG tự chọn thị trường -> để mặc định hiện placeholder "Chọn thị trường".
      })
      // `translate` (không phải hook `t`) để effect chạy-một-lần không phải khai `t`
      // làm phụ thuộc — khai vào thì đổi ngôn ngữ sẽ tải lại danh mục vô ích.
      .catch((e) =>
        setPageError(translate("toast.marketsFailed") + friendly(e)),
      );
    getOcrDpi()
      .then((d) => {
        setDpi(d.value);
        setDpiBounds({ min: d.min, max: d.max });
        setDpiTier({ vi: d.tier_label ?? "", en: d.tier_label_en ?? d.tier_label ?? "" });
      })
      .catch(() => {});
  }, []);

  const marketObj = useMemo(
    () => markets.find((m) => m.id === marketId) || null,
    [markets, marketId],
  );
  // Bước 2 — QUỐC GIA/VÙNG LÃNH THỔ của khu vực đang chọn. Mỗi quốc gia thuộc đúng
  // một thị trường (bộ quy định), nên chọn quốc gia là suy ra luôn thị trường.
  const countryOptions = useMemo(() => {
    if (!regionId || regionId === KHAC) return [];
    return markets.flatMap((m) =>
      (m.countries || [])
        .filter((c) => c.region_id === regionId)
        .map((c) => ({ value: `${m.id}|${c.id}`, label: cat(c.name) })),
    );
  }, [markets, regionId, cat]);
  // Loại hình lao động: các mục con của ĐÚNG thị trường đang chọn + "khác".
  //  - Chưa chọn thị trường -> không có tùy chọn (chỉ hiện placeholder).
  //  - Thị trường = "Khác" -> chỉ có tùy chọn "Khác" để người dùng tự nhập loại hình.
  const jobTypeOptions = useMemo(() => {
    const khac = { id: KHAC, name: KHAC };
    if (marketId === KHAC) return [khac];
    if (!marketObj) return [];
    return [...marketObj.job_types, khac];
  }, [marketObj, marketId]);

  // Thị trường KHÔNG có bước quốc gia (vd Biển quốc tế) gắn thẳng vào khu vực.
  const regionMarket = useMemo(
    () =>
      markets.find(
        (m) => m.region_id === regionId && !(m.countries || []).length,
      ) || null,
    [markets, regionId],
  );

  // Đổi KHU VỰC -> xóa quốc gia + loại hình (buộc chọn lại theo đúng nhánh).
  // Khu vực gắn thẳng thị trường (Biển quốc tế) -> chọn luôn thị trường, bỏ bước quốc gia.
  function onChangeRegion(id: string) {
    setRegionId(id);
    setCountryId("");
    // Khu vực "Khác" -> hệ thống không có danh mục nghề nào để chọn, nên loại hình
    // lao động CHỈ có thể là "Khác": chọn sẵn luôn thay vì bắt bấm thêm một lần.
    setJobTypeId(id === KHAC ? KHAC : "");
    const direct = markets.find(
      (m) => m.region_id === id && !(m.countries || []).length,
    );
    setMarketId(id === KHAC ? KHAC : direct ? direct.id : "");
  }

  // Đổi QUỐC GIA -> suy ra thị trường; reset loại hình lao động.
  function onChangeCountry(value: string) {
    const [mid, cid] = value.split("|");
    setMarketId(mid || "");
    setCountryId(cid || "");
    setJobTypeId("");
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    const pdfs = Array.from(list).filter(
      (f) =>
        f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"),
    );
    const tooBig = pdfs.filter((f) => f.size > MAX_FILE_MB * 1048576);
    if (tooBig.length)
      notify("error", `${t("up.errFileTooBig")} (${MAX_FILE_MB} MB): ${tooBig.map((f) => f.name).join(", ")}`);
    const incoming = pdfs.filter((f) => f.size <= MAX_FILE_MB * 1048576);
    setFiles((prev) => {
      const map = new Map(prev.map((f) => [f.name + f.size, f]));
      for (const f of incoming) map.set(f.name + f.size, f);
      const next = Array.from(map.values());
      if (next.length > MAX_FILES) {
        notify("error", `${t("up.errTooManyFiles")} (${MAX_FILES}).`);
        return next.slice(0, MAX_FILES);
      }
      return next;
    });
    if (incoming.length) setPickerOpen(false); // chọn xong -> thu vùng chọn lại
  }

  function saveDpi(next: number) {
    const v = Math.round(next);
    setDpi(v);
    setDpiSaving(true);
    setOcrDpi(v)
      .then((d) => {
        setDpi(d.value);
        setDpiBounds({ min: d.min, max: d.max });
        setDpiTier({ vi: d.tier_label ?? "", en: d.tier_label_en ?? d.tier_label ?? "" });
      })
      .catch((e) => notify("error", t("toast.dpiFailed") + friendly(e)))
      .finally(() => setDpiSaving(false));
  }

  // Nhãn bậc DPI theo ngôn ngữ đang bật (backend trả cả hai bản).
  const tierLabel = lang === "en" ? dpiTier.en : dpiTier.vi;

  const marketOtherBad = marketId === KHAC && !meaningful(marketOther);
  const jobTypeOtherBad = jobTypeId === KHAC && !meaningful(jobTypeOther);
  // Đã chọn xong 3 bước (khu vực -> quốc gia -> loại hình) thì mới cho chọn file.
  const choiceDone =
    !!regionId &&
    !!marketId &&
    (marketId === KHAC || !!countryId || !!regionMarket) &&
    !!jobTypeId &&
    !marketOtherBad &&
    !jobTypeOtherBad;
  const canSubmit = choiceDone && files.length > 0;

  async function onConfirm() {
    setPageError("");
    if (!regionId) return setPageError(t("up.errRegion"));
    if (marketId !== KHAC && !countryId && !regionMarket)
      return setPageError(t("up.errCountry"));
    if (!jobTypeId) return setPageError(t("up.errJobType"));
    if (files.length === 0)
      return setPageError(t("up.errFiles"));
    if (marketOtherBad)
      return setPageError(t("up.errMarketOther"));
    if (jobTypeOtherBad)
      return setPageError(t("up.errJobTypeOther"));
    if (files.reduce((s, f) => s + f.size, 0) > MAX_TOTAL_MB * 1048576)
      return setPageError(`${t("up.errTotalTooBig")} (${MAX_TOTAL_MB} MB).`);
    notify("info", `${t("toast.ocrStart")} ${files.length} file…`);
    // Chạy qua KHO TOÀN CỤC: SSE + request sống ngoài component -> chuyển trang
    // không mất tiến trình; kết quả/lỗi được useEffect phía trên xử lý.
    startUploadJob(files, {
      market: marketId,
      country: marketId === KHAC ? "" : countryId,
      job_type: jobTypeId,
      market_other: marketId === KHAC ? marketOther.trim() : "",
      job_type_other: jobTypeId === KHAC ? jobTypeOther.trim() : "",
    });
  }

  const totalKB = Math.round(files.reduce((s, f) => s + f.size, 0) / 1024);

  return (
    <AppShell step={1}>
      {error ? <Alert kind="error">{error}</Alert> : null}

      <div className={CARD + " mx-auto grid max-w-380 gap-5"}>
        <h2>{t("up.title")}</h2>
        <p className="mt-0 text-sm text-slate-500">{t("up.lead")}</p>

        {/* Thị trường + Loại hình lao động (trái) | Chế độ (phải) */}
        <div className="grid grid-cols-[2fr_1fr] items-start gap-6">
          <div className="grid gap-3">
            <Pick
              label={t("up.region")}
              placeholder={t("up.regionPick")}
              searchPlaceholder={t("common.search")}
              emptyText={t("common.noMatch")}
              otherLabel={t("common.other")}
              value={regionId}
              onChange={onChangeRegion}
              disabled={loading}
              /* `cat(...)`: danh mục khai theo quy ước "English (Tiếng Việt)" —
                 hiện đúng MỘT bản theo ngôn ngữ đang chọn, không đổ cả hai vào
                 một dòng. Xem `catLabel` trong i18n.ts. */
              options={regions.map((r) => ({ value: r.id, label: cat(r.name) }))}
              withOther
            />

            {regionId && regionId !== KHAC && !regionMarket ? (
              <Pick
                label={t("up.country")}
                placeholder={t("up.countryPick")}
                searchPlaceholder={t("common.search")}
                emptyText={t("common.noMatch")}
                otherLabel={t("common.other")}
                value={marketId && countryId ? `${marketId}|${countryId}` : ""}
                onChange={onChangeCountry}
                disabled={loading || !countryOptions.length}
                options={countryOptions}
              />
            ) : null}

            {marketId === KHAC ? (
              <OtherInput
                value={marketOther}
                onChange={setMarketOther}
                disabled={loading}
                invalid={marketOtherBad}
                badText={t("up.otherBad")}
                placeholder={t("up.marketOther")}
              />
            ) : null}

            {/* Loại hình lao động chỉ hiện khi ĐÃ chọn xong khu vực + quốc gia
                (hoặc khu vực "Khác") — tránh chọn nhầm bộ quy định. */}
            {marketId && (marketId === KHAC || countryId || regionMarket) ? (
              <Pick
                label={t("up.jobType")}
                placeholder={t("up.jobTypePick")}
                searchPlaceholder={t("common.search")}
                emptyText={t("common.noMatch")}
                otherLabel={t("common.other")}
                value={jobTypeId}
                onChange={setJobTypeId}
                disabled={!marketId || loading}
                options={jobTypeOptions
                  .filter((t) => t.id !== KHAC)
                  .map((t) => ({ value: t.id, label: cat(t.name) }))}
                withOther={jobTypeOptions.some((t) => t.id === KHAC)}
              />
            ) : null}

            {jobTypeId === KHAC ? (
              <OtherInput
                value={jobTypeOther}
                onChange={setJobTypeOther}
                disabled={loading}
                invalid={jobTypeOtherBad}
                badText={t("up.otherBad")}
                placeholder={t("up.jobTypeOther")}
              />
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2 text-sm font-semibold">
              {t("up.mode")}
              {dpiSaving ? <Spinner dark /> : null}
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="mb-1 flex items-center justify-between text-[13px] font-medium text-slate-700">
                <span>{t("up.dpi")}</span>
                <span className="flex items-center gap-1.5">
                  {tierLabel ? (
                    <span className="rounded bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700">
                      {tierLabel}
                    </span>
                  ) : null}
                  <span className="rounded bg-white px-2 py-0.5 font-mono text-xs text-blue-700">
                    {dpi}
                  </span>
                </span>
              </div>
              <input
                type="range"
                min={dpiBounds.min}
                max={dpiBounds.max}
                step={20}
                value={dpi}
                disabled={dpiSaving || loading}
                onChange={(e) => setDpi(Number(e.target.value))}
                onMouseUp={(e) =>
                  saveDpi(Number((e.target as HTMLInputElement).value))
                }
                onTouchEnd={(e) =>
                  saveDpi(Number((e.target as HTMLInputElement).value))
                }
                className="w-full"
              />
              <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                <span>{t("up.dpiFast")} ({dpiBounds.min})</span>
                <span>{t("up.dpiAccurate")} ({dpiBounds.max})</span>
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-500">
                {t("up.dpiNote")}
              </p>
            </div>
          </div>
        </div>

        <div>
          <div className="mb-1.5 text-sm font-semibold">
            {t("up.files")}
          </div>
          {/* Vùng chọn file: ẨN khi đã có file; bấm "Thay đổi" để mở lại.
              KHÓA khi chưa chọn xong khu vực + quốc gia + loại hình lao động —
              vì bộ trường trích xuất và quy định đối chiếu phụ thuộc các lựa chọn này. */}
          {!files.length || pickerOpen ? (
            <label
              onDragOver={(e) => {
                e.preventDefault();
                if (choiceDone) setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                if (!loading && choiceDone) addFiles(e.dataTransfer.files);
              }}
              className={
                "flex flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors " +
                (!choiceDone
                  ? "cursor-not-allowed border-slate-200 bg-slate-100 opacity-60"
                  : dragOver
                    ? "cursor-pointer border-blue-500 bg-blue-50"
                    : "cursor-pointer border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-blue-50/50")
              }
            >
              <IconUpload className="h-8 w-8 text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">
                {choiceDone ? t("up.drop") : t("up.dropLocked")}
              </span>
              <span className="text-xs text-slate-500">
                {choiceDone ? t("up.dropHint") : t("up.dropLockedHint")}
              </span>
              <input
                type="file"
                accept="application/pdf"
                multiple
                disabled={loading || !choiceDone}
                className="hidden"
                onChange={(e) => addFiles(e.target.files)}
              />
            </label>
          ) : null}

          {files.length ? (
            <div className="mt-3">
              <div className="mb-1 flex items-center gap-3 text-xs font-semibold text-slate-600">
                <span>
                  {t("up.chosen")} {files.length} file ({totalKB} KB):
                </span>
                {/* Nút chữ, không nền, màu xanh: mở lại vùng chọn để thêm/đổi file */}
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => setPickerOpen((v) => !v)}
                  className={
                    "bg-transparent p-0 text-xs font-semibold " +
                    (loading
                      ? "cursor-not-allowed text-slate-300"
                      : "text-blue-600 hover:underline")
                  }
                >
                  {pickerOpen ? t("common.close") : t("common.change")}
                </button>
                {/* Bỏ chọn TẤT CẢ file: chỉ hiện cùng nút "Đóng" (vùng tải file đang mở),
                    trang trí giống nút Đóng, căn sát phải nút Đóng. */}
                {pickerOpen ? (
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => setFiles([])}
                    className={
                      "bg-transparent p-0 text-xs font-semibold " +
                      (loading
                        ? "cursor-not-allowed text-slate-300"
                        : "text-blue-600 hover:underline")
                    }
                    title={t("up.clearAllTitle")}
                  >
                    {t("up.clearAll")}
                  </button>
                ) : null}
              </div>
              <ul className="flex flex-col gap-1">
                {files.map((f, i) => (
                  <li
                    key={f.name + f.size}
                    className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs"
                  >
                    <span className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-slate-600">
                        {f.name} ({Math.round(f.size / 1024)} KB)
                      </span>
                      {roleMapView[f.name] ? (
                        <span className="shrink-0 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700">
                          {roleMapView[f.name]}
                        </span>
                      ) : null}
                    </span>
                    <button
                      type="button"
                      disabled={loading}
                      onClick={() =>
                        setFiles((prev) => prev.filter((_, j) => j !== i))
                      }
                      className={
                        "ml-2 shrink-0 rounded px-1.5 " +
                        (loading
                          ? "cursor-not-allowed text-slate-300"
                          : "text-slate-400 hover:bg-slate-100 hover:text-red-600")
                      }
                      title={loading ? t("up.removeLocked") : t("up.removeFile")}
                    >
                      <IconX className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        {/* 3 phần căn đều: tiến độ (TRÁI) — ước tính thời gian (GIỮA) — nút (PHẢI) */}
        <div className="mt-4 flex items-center gap-3">
          <span className="flex-1 text-left text-xs font-medium text-slate-500">
            {loading && progress ? progress : ""}
          </span>
          <span className="flex-1 text-center text-xs font-medium text-blue-600">
            {loading && eta ? eta : ""}
          </span>
          <span className="flex flex-1 justify-end">
            <button
              className={BTN_PRIMARY}
              disabled={loading || !canSubmit}
              onClick={onConfirm}
            >
              {loading ? (
                <>
                  <Spinner />
                  {t("up.running")}
                </>
              ) : (
                <>{t("up.submit")}</>
              )}
            </button>
          </span>
        </div>
      </div>
    </AppShell>
  );
}
