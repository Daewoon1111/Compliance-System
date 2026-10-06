// Lớp Tailwind dùng chung giữa các trang. Chỉ export hằng/hàm, không export
// component — Fast Refresh cần một file thuần loại này hoặc loại kia.

import { C, TONE, toneOfVerdict } from "./colors";
import type { JsonValue } from "./types";

export const CARD = `rounded-xl border ${C.border} ${C.surface} p-4 shadow-sm`;

/**
 * NÚT CÓ CHỮ (nền + viền). Lớp `btn-hl` khai màu DI CHUỘT theo chế độ giao diện
 * trong index.css: nền tối -> nền ĐỎ chữ trắng, nền sáng -> nền XANH chữ trắng.
 * Phải khai bằng CSS vì màu phụ thuộc `.dark`, không viết được trong một chuỗi
 * class Tailwind duy nhất.
 */
export const BTN =
  `btn-hl inline-flex items-center justify-center cursor-pointer rounded-lg border ${C.border} ` +
  `${C.surface} px-4.5 py-[9px] text-sm font-semibold ${C.ink} transition-colors ` +
  "disabled:opacity-50 disabled:cursor-not-allowed";

/** Nút CHÍNH (nền màu nhấn). Lớp `btn-accent` khai màu nền + màu di chuột theo chế
 *  độ trong index.css: nền sáng giữ XANH (hover bạc, chữ đen); nền tối đổi sang
 *  CAM ĐỎ #FF6347 (hover xanh nhạt #E0FFFF, chữ đen) cho nổi trên nền tối. */
export const BTN_PRIMARY =
  "btn-accent inline-flex items-center justify-center cursor-pointer rounded-lg border border-blue-600 " +
  "bg-blue-600 px-4.5 py-[9px] text-sm font-semibold text-white transition-colors " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

export const FIELD =
  `w-full rounded-lg border ${C.border} ${C.surface} px-3 py-2 ${C.ink} ` +
  "focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500";

/** Badge KẾT LUẬN — màu lấy từ bộ quy tắc màu (`colors.ts`), không tự chọn sắc độ.
 *
 * `whitespace-nowrap`: nhãn kết luận là một CỤM ĐỌC MỘT LẦN ("Theo luật nước tiếp
 * nhận"). Cho nó vỡ dòng trong viên nang bo tròn thì chữ bị bẻ giữa cụm và viên
 * nang cao gấp đôi, làm lệch cả hàng bảng. Cột chứa badge phải tự nới theo. */
export function badgeCls(v: string): string {
  return (
    "inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-bold tracking-wide " +
    TONE[toneOfVerdict(v)]
  );
}

/** Hiển thị giá trị trường gọn: tiền {amount,currency,period,raw} -> '550 USD/tháng'.
 * KHÔNG BAO GIỜ in JSON thô cho người dùng: object không đọc được -> empty.
 *
 * `withPeriod=false`: bỏ kỳ trả ('/tháng') — dùng cho CÁC KHOẢN CHI PHÍ. Chi phí là
 * khoản thu/chi MỘT LẦN cho cả hợp đồng (tiền dịch vụ, visa, khám sức khỏe...), ghi
 * '0 VND/tháng' là sai bản chất. Chỉ TIỀN LƯƠNG mới có kỳ trả theo tháng. */
/** Đơn vị tiền hiển thị theo lối viết Việt Nam: nội bộ lưu "VND" (ASCII, an toàn cho
 *  regex/so khớp), nhưng trên hồ sơ và giao diện phải là "VNĐ". */
function fmtCurrency(c: JsonValue | undefined): string {
  return String(c ?? "").toUpperCase() === "VND" ? "VNĐ" : String(c ?? "");
}

export function fmtFieldValue(
  v: JsonValue | undefined, empty = "—", withPeriod = true,
): string {
  if (v === null || v === undefined || v === "") return empty;
  if (typeof v === "object") {
    const o = v as Record<string, JsonValue>;
    // ƯU TIÊN amount: raw có thể thiếu con số ('VND/tháng') trong khi amount=0 hợp lệ
    // -> phải hiện '0 VNĐ', không hiện raw cụt.
    // CHỈ nhận số (kể cả chuỗi số của phiên cũ). Chữ lọt vào ô số ("Không có") không
    // được ghép với đơn vị tiền -> tránh hiện "Không có VNĐ"; rơi xuống nhánh raw.
    const amt = typeof o.amount === "number" ? o.amount
      : (typeof o.amount === "string" && o.amount.trim() && !Number.isNaN(Number(o.amount))
          ? Number(o.amount) : null);
    // GHI CHÚ trong ngoặc đi kèm số tiền trên hợp đồng: mức quy đổi thứ hai
    // ('1.150 JPY/giờ') hoặc LÝ DO khoản thu ('Phí trả cho đại lý làm visa') — bỏ đi
    // là bỏ đúng chỗ quyết định khoản đó có hợp lệ hay không.
    const note = typeof o.note === "string" && o.note.trim() ? ` (${o.note.trim()})` : "";
    if (amt !== null) {
      const money = [amt.toLocaleString("vi-VN"), fmtCurrency(o.currency)]
        .filter((x) => x !== "").join(" ");
      return (withPeriod && typeof o.period === "string" && o.period
        ? `${money}/${o.period}` : money) + note;
    }
    if (typeof o.raw === "string" && o.raw.trim()) {
      // raw thô có thể dính kỳ trả ('0 VND/tháng') -> cắt bỏ khi không cần kỳ trả.
      const raw = o.raw.trim();
      return (withPeriod ? raw : raw.replace(/\s*\/\s*(tháng|năm|tuần|ngày|giờ|month|year|week|day|hour)\b.*$/i, "")) + note;
    }
    // Vỏ rỗng ({amount:null,...}) hoặc object lạ -> coi như trống.
    return empty;
  }
  return String(v);
}

/** Giá trị MỘT KHOẢN CHI PHÍ: chỉ 'số tiền + đơn vị tiền tệ', không kèm kỳ trả. */
export function fmtCostValue(v: JsonValue | undefined, empty = "—"): string {
  return fmtFieldValue(v, empty, false);
}

/** NHÃN "Loại hình công việc" — loại hình đã chọn kèm TÊN CÔNG VIỆC đọc được từ hợp
 *  đồng: `Lao động kỹ năng đặc định - "Nông nghiệp"`.
 *
 *  Loại hình là thứ người dùng CHỌN ở trang 1 (quyết định bộ trường và bộ quy định);
 *  tên công việc là thứ hợp đồng GHI. Hai thứ khác nhau nhưng người duyệt cần đọc
 *  cùng lúc để thấy ngay hồ sơ có đúng loại hình đã chọn hay không. Không trích được
 *  tên công việc thì giữ nguyên nhãn cũ, không thêm dấu ngoặc rỗng. */
export function jobTypeText(jobType: string, jobTitle?: string): string {
  const base = (jobType || "").trim();
  const name = (jobTitle || "").trim();
  if (!name) return base || "—";
  return base ? `${base} - "${name}"` : `"${name}"`;
}
