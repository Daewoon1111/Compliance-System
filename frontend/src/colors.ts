/**
 * BỘ QUY TẮC MÀU — nguồn sự thật DUY NHẤT cho cả giao diện sáng và tối.
 *
 * Vì sao cần: không có nó thì màu nằm rải ở ba nơi — chuỗi Tailwind trong JSX
 * (`bg-white text-slate-800`), bảng ánh xạ `.dark .bg-white {…}` trong index.css,
 * và vài mã hex ghim cứng trong CSS cho nút/thanh kéo. Đổi một màu phải nhớ sửa
 * đủ ba chỗ, và mỗi trang mới lại phải nhớ dùng đúng sắc độ đã dùng ở trang cũ.
 *
 * Cách làm: mỗi màu được gọi theo VAI TRÒ (nền thẻ, chữ phụ, viền, màu nhấn,
 * màu di chuột…) chứ không theo sắc độ. Giá trị thật của từng vai trò khai một
 * lần bằng biến CSS trong `index.css` (`:root` = sáng, `.dark` = tối); file này
 * chỉ ghép biến đó thành lớp Tailwind dùng được ngay trong JSX.
 *
 * Đổi bảng màu = sửa biến trong `index.css`. Thêm chỗ dùng = lấy token ở đây.
 * KHÔNG viết mã màu trực tiếp trong JSX nữa.
 *
 * ┌ Vai trò ─────────┬ Sáng ───────────────┬ Tối ────────────────┐
 * │ surface          │ trắng               │ xanh đen            │
 * │ surface-subtle   │ slate-50            │ xanh đen nhạt hơn   │
 * │ border           │ slate-200           │ xám xanh            │
 * │ ink / ink-muted  │ slate-800 / 500     │ slate-200 / 400     │
 * │ accent           │ XANH #2563eb        │ ĐỎ #ff0000          │
 * │ hover            │ nền xanh, chữ trắng │ nền đỏ, chữ trắng   │
 * │ ok / bad / warn  │ xanh lá / đỏ / cam  │ cùng sắc, tối lại   │
 * └──────────────────┴─────────────────────┴─────────────────────┘
 */

/** Lớp nền + chữ + viền theo VAI TRÒ. Ghép thẳng vào `className`. */
export const C = {
  /** Nền thẻ/bảng chính. */
  surface: "bg-[var(--c-surface)]",
  /** Nền phụ: đầu bảng, ô chỉ đọc, dải tiêu đề. */
  surfaceSubtle: "bg-[var(--c-surface-subtle)]",
  /** Viền mặc định của thẻ, bảng, ô nhập. */
  border: "border-[var(--c-border)]",
  /** Viền nhạt: đường kẻ giữa các dòng trong bảng. */
  borderSoft: "border-[var(--c-border-soft)]",
  /** Chữ chính. */
  ink: "text-[var(--c-ink)]",
  /** Chữ phụ: nhãn, chú thích, đơn vị. */
  inkMuted: "text-[var(--c-ink-muted)]",
  /** Chữ mờ: giá trị trống, gợi ý nhập. */
  inkFaint: "text-[var(--c-ink-faint)]",
  /** Màu nhấn: nút chính, liên kết, mục đang mở. */
  accent: "text-[var(--c-accent)]",
  accentBg: "bg-[var(--c-accent)]",
  accentBorder: "border-[var(--c-accent)]",
} as const;

/** Nền + chữ của một TRẠNG THÁI kết luận (dùng cho badge, dòng bảng, thẻ chi tiết). */
export const TONE = {
  ok: "bg-[var(--c-ok-bg)] text-[var(--c-ok-ink)]",
  bad: "bg-[var(--c-bad-bg)] text-[var(--c-bad-ink)]",
  warn: "bg-[var(--c-warn-bg)] text-[var(--c-warn-ink)]",
  info: "bg-[var(--c-info-bg)] text-[var(--c-info-ink)]",
  neutral: "bg-[var(--c-surface-subtle)] text-[var(--c-ink-muted)]",
} as const;

export type ToneName = keyof typeof TONE;

/** Kết luận -> tông màu. Một chỗ duy nhất quyết định "màu của một verdict". */
export function toneOfVerdict(v: string): ToneName {
  if (v === "PASS") return "ok";
  if (v === "FAIL") return "bad";
  if (v === "NEEDS_SUPPLEMENT") return "warn";
  return "neutral";
}
