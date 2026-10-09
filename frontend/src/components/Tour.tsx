import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { IconArrowLeft, IconArrowRight, IconCheck, IconX } from "./Icons";
import { BTN, BTN_GHOST, BTN_PRIMARY } from "../ui";
import { useT } from "../i18n";

/** Một bước hướng dẫn: `target` = giá trị `data-tour` của vùng cần khoanh (bỏ trống =
 *  thẻ giữa màn hình, dùng cho lời chào). Bước nào không có vùng trên trang đang mở thì
 *  bị bỏ qua — cùng một danh sách dùng được cho mọi trang. */
type Step = { target?: string; key: string };

const STEPS: Step[] = [
  { key: "intro" },
  { target: "nav-home", key: "home" },
  { target: "nav-check", key: "check" },
  { target: "up-checkset", key: "upCheckset" },
  { target: "up-files", key: "upFiles" },
  { target: "up-options", key: "upOptions" },
  { target: "up-submit", key: "upSubmit" },
  { target: "nav-history", key: "history" },
  { target: "nav-config", key: "config" },
  { target: "nav-regsets", key: "regsets" },
  { target: "nav-stats", key: "stats" },
  { target: "notes", key: "notes" },
  { target: "modes", key: "modes" },
  { target: "settings", key: "settings" },
  { target: "help", key: "help" },
];

const PAD = 6;       // khoảng hở quanh vùng được khoanh
const GAP = 14;      // khoảng cách thẻ ghi chú tới vùng
const CARD_W = 360;
const CARD_H = 210;  // ước lượng chiều cao thẻ để chọn phía đặt

function elOf(target?: string): HTMLElement | null {
  return target ? document.querySelector<HTMLElement>(`[data-tour="${target}"]`) : null;
}

/** Vùng hiển thị thật (bỏ phần tử đang ẩn / kích thước 0). */
function visible(el: HTMLElement | null): boolean {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

/** Chỗ đặt thẻ ghi chú: phải -> dưới -> trên -> trái của vùng, luôn nằm trong màn hình. */
function placeCard(r: DOMRect | null): { top: number; left: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.min(CARD_W, vw - 32);
  if (!r) return { top: Math.max(16, vh / 2 - CARD_H / 2), left: Math.max(16, vw / 2 - w / 2) };
  const clampX = (x: number) => Math.min(Math.max(16, x), vw - w - 16);
  const clampY = (y: number) => Math.min(Math.max(16, y), vh - CARD_H - 16);
  if (r.right + GAP + w <= vw - 16) return { top: clampY(r.top), left: r.right + GAP };
  if (r.bottom + GAP + CARD_H <= vh - 16) return { top: r.bottom + GAP, left: clampX(r.left) };
  if (r.top - GAP - CARD_H >= 16) return { top: r.top - GAP - CARD_H, left: clampX(r.left) };
  return { top: clampY(r.top), left: Math.max(16, r.left - GAP - w) };
}

/**
 * HƯỚNG DẪN SỬ DỤNG — phủ tối cả màn hình, khoét sáng đúng vùng đang giới thiệu, thẻ ghi
 * chú bên cạnh có Trở lại / Tiếp theo. ← → và Esc dùng được từ bàn phím.
 */
export default function Tour({ onClose }: { onClose: () => void }) {
  const t = useT();
  // Chốt danh sách bước LÚC MỞ theo các vùng đang có trên trang.
  const steps = useMemo(() => STEPS.filter((s) => !s.target || visible(elOf(s.target))), []);
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const step = steps[i];
  const last = i === steps.length - 1;

  const measure = useCallback(() => {
    const el = elOf(step?.target);
    setRect(el ? el.getBoundingClientRect() : null);
  }, [step]);

  // Đổi bước: cuộn vùng vào tầm nhìn rồi đo ở khung hình kế tiếp (sau khi cuộn xong).
  useLayoutEffect(() => {
    const el = elOf(step?.target);
    if (el) el.scrollIntoView({ block: "nearest", inline: "nearest" });
    const id = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(id);
  }, [measure, step]);

  useEffect(() => {
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [measure]);

  useEffect(() => {
    // Pha CAPTURE + chặn lan: phím ← → / Esc dành cho hướng dẫn, không được lọt xuống ô
    // đang có tiêu điểm phía dưới (thanh trượt độ nét sẽ đổi giá trị theo từng phím).
    const h = (e: KeyboardEvent) => {
      if (e.key !== "Escape" && e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") setI((v) => Math.min(v + 1, steps.length - 1));
      else setI((v) => Math.max(v - 1, 0));
    };
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [onClose, steps.length]);

  if (!step) return null;
  const pos = placeCard(rect);

  return (
    <div role="dialog" aria-modal="true" aria-label={t("tour.title")}>
      {/* Lớp chặn bấm vào trang phía dưới trong lúc xem hướng dẫn. */}
      <div className="fixed inset-0 z-[89]" />
      {rect ? (
        <div
          className="tour-hole"
          style={{ top: rect.top - PAD, left: rect.left - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2 }}
        />
      ) : (
        <div className="fixed inset-0 z-[90] bg-slate-900/60" />
      )}
      <div className="tour-card rise-in rounded-2xl border border-slate-300 bg-surface p-5 shadow-2xl" style={pos}>
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-[11.5px] font-bold uppercase tracking-wide text-blue-700">
              {t("tour.title")} · {i + 1}/{steps.length}
            </div>
            <h3 className="m-0 mt-1 text-[16px] font-extrabold text-slate-800">{t(`tour.${step.key}.title`)}</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-lg border-0 bg-transparent text-slate-400 hover:bg-slate-100 hover:text-slate-800"
            title={t("tour.skip")}
            aria-label={t("tour.skip")}
          >
            <IconX className="h-4.5 w-4.5" />
          </button>
        </div>
        <p className="m-0 mt-2 text-[13.5px] leading-relaxed text-slate-600">{t(`tour.${step.key}.body`)}</p>
        <div className="mt-3 flex gap-1" aria-hidden="true">
          {steps.map((s, k) => (
            <span key={s.key} className={"h-1.5 rounded-full transition-all " + (k === i ? "w-5 bg-blue-600" : "w-1.5 bg-slate-300")} />
          ))}
        </div>
        <div className="mt-3 flex items-center justify-end gap-2 whitespace-nowrap">
          {i > 0 ? (
            <button type="button" className={BTN + " !px-3 !py-1.5 text-[13px]"} onClick={() => setI(i - 1)}>
              <IconArrowLeft className="h-4 w-4" /> {t("tour.back")}
            </button>
          ) : (
            <button type="button" className={BTN_GHOST + " !px-3 !py-1.5 text-[13px]"} onClick={onClose}>{t("tour.skip")}</button>
          )}
          {last ? (
            <button type="button" className={BTN_PRIMARY + " !px-3.5 !py-1.5 text-[13px]"} onClick={onClose} autoFocus>
              <IconCheck className="h-4 w-4" /> {t("tour.done")}
            </button>
          ) : (
            <button type="button" className={BTN_PRIMARY + " !px-3.5 !py-1.5 text-[13px]"} onClick={() => setI(i + 1)} autoFocus>
              {t("tour.next")} <IconArrowRight className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
