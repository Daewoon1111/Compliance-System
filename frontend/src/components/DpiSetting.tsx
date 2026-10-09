import { useEffect, useState } from "react";
import { friendly, getOcrDpi, setOcrDpi } from "../api/client";
import { notify } from "../notify";
import { useLang, useT } from "../i18n";
import { Spinner } from "./Layout";

/**
 * ĐỘ NÉT KHI ĐỌC TÀI LIỆU (DPI) — một nút duy nhất; số ô ảnh của bộ đọc và số đoạn quy
 * định đem đối chiếu đi theo bảng bậc ở backend (backend là nguồn duy nhất của bảng đó).
 * Giá trị được backend NHỚ qua lần mở lại phần mềm.
 */
export default function DpiSetting({ disabled = false }: { disabled?: boolean }) {
  const t = useT();
  const lang = useLang();
  const [dpi, setDpi] = useState(200);
  const [bounds, setBounds] = useState({ min: 120, max: 300 });
  const [tier, setTier] = useState({ vi: "", en: "" });
  const [saving, setSaving] = useState(false);

  function take(d: { value: number; min: number; max: number; tier_label?: string; tier_label_en?: string }) {
    setDpi(d.value);
    setBounds({ min: d.min, max: d.max });
    setTier({ vi: d.tier_label ?? "", en: d.tier_label_en ?? d.tier_label ?? "" });
  }

  useEffect(() => {
    getOcrDpi().then(take).catch(() => {});
  }, []);

  function save(next: number) {
    const v = Math.round(next);
    setDpi(v);
    setSaving(true);
    setOcrDpi(v)
      .then(take)
      .catch((e) => notify("error", t("toast.dpiFailed") + friendly(e)))
      .finally(() => setSaving(false));
  }

  const tierLabel = lang === "en" ? tier.en : tier.vi;
  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[13.5px] font-bold text-slate-800">{t("up.dpi")}</div>
          {tierLabel ? <div className="mt-0.5 text-[12.5px] font-bold text-blue-700">{tierLabel}</div> : null}
        </div>
        <span className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-surface px-2.5 py-1 font-mono text-[12.5px] font-semibold text-slate-700">
          {saving ? <Spinner dark /> : null}
          {dpi} DPI
        </span>
      </div>
      <input
        type="range"
        aria-label={t("up.dpi")}
        min={bounds.min}
        max={bounds.max}
        step={20}
        value={dpi}
        disabled={saving || disabled}
        onChange={(e) => setDpi(Number(e.target.value))}
        onMouseUp={(e) => save(Number((e.target as HTMLInputElement).value))}
        onTouchEnd={(e) => save(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => save(Number((e.target as HTMLInputElement).value))}
        className="mt-4 w-full"
      />
      <div className="mt-1 flex justify-between text-[11.5px] text-slate-400">
        <span>{t("up.dpiFast")} · {bounds.min}</span>
        <span>{t("up.dpiAccurate")} · {bounds.max}</span>
      </div>
      <p className="m-0 mt-3 border-t border-slate-200 pt-2.5 text-[12px] leading-snug text-slate-500">{t("up.dpiNote")}</p>
    </div>
  );
}
