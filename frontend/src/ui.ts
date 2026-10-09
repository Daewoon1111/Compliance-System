// Lớp Tailwind dùng chung giữa các trang. Chỉ export hằng/hàm, không export
// component — Fast Refresh cần một file thuần loại này hoặc loại kia.

import { C, TONE, toneOfVerdict } from "./colors";
import type { JsonValue } from "./types";

/** THẺ: nền thẻ, viền mảnh, bo 16px, bóng mềm (khai trong index.css `.surface-card`). */
export const CARD = "surface-card p-5";

/**
 * NÚT PHỤ (nền phụ + viền). Lớp `btn-hl` khai màu DI CHUỘT trong index.css theo biến
 * màu, nên đúng ở cả giao diện sáng lẫn tối.
 */
export const BTN =
  `btn-hl inline-flex items-center justify-center gap-2 cursor-pointer rounded-[10px] border ` +
  `border-slate-300 bg-slate-100 px-4 py-2 text-sm font-bold ${C.ink} transition-all ` +
  "disabled:opacity-50 disabled:cursor-not-allowed";

/** NÚT CHÍNH (nền màu nhấn + quầng sáng). Một trang chỉ nên có MỘT nút loại này. */
export const BTN_PRIMARY =
  "btn-accent inline-flex items-center justify-center gap-2 cursor-pointer rounded-[10px] border border-blue-600 " +
  "bg-blue-600 px-5 py-2 text-sm font-bold text-white transition-all " +
  "disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none";

/** NÚT TRƠN — chỉ chữ + biểu tượng, cho thao tác phụ cạnh nút chính (Hủy, Trở lại). */
export const BTN_GHOST =
  "inline-flex items-center justify-center gap-2 cursor-pointer rounded-[10px] border border-slate-200 " +
  "bg-transparent px-4 py-2 text-sm font-bold text-slate-500 transition-colors hover:border-slate-300 " +
  "hover:bg-slate-50 hover:text-slate-800 disabled:opacity-50 disabled:cursor-not-allowed";

export const FIELD =
  "w-full rounded-[9px] border border-slate-200 bg-slate-50 px-3 py-2 text-slate-800 " +
  "transition-[border-color,box-shadow]";

/** Badge KẾT LUẬN — màu lấy từ bộ quy tắc màu (`colors.ts`), không tự chọn sắc độ.
 *
 * `whitespace-nowrap`: nhãn kết luận là một CỤM ĐỌC MỘT LẦN ("Cần bổ sung"). Cho
 * nó vỡ dòng trong viên nang bo tròn thì chữ bị bẻ giữa cụm và viên
 * nang cao gấp đôi, làm lệch cả hàng bảng. Cột chứa badge phải tự nới theo. */
export function badgeCls(v: string): string {
  return (
    "inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-bold tracking-wide " +
    TONE[toneOfVerdict(v)]
  );
}

/** Hiển thị giá trị trường gọn: tiền {amount,currency,period,raw} -> '120.000.000 VNĐ'.
 * KHÔNG BAO GIỜ in JSON thô cho người dùng: object không đọc được -> empty. */
/** Đơn vị tiền hiển thị theo lối viết Việt Nam: nội bộ lưu "VND" (ASCII, an toàn cho
 *  regex/so khớp), nhưng trên hồ sơ và giao diện phải là "VNĐ". */
function fmtCurrency(c: JsonValue | undefined): string {
  return String(c ?? "").toUpperCase() === "VND" ? "VNĐ" : String(c ?? "");
}

export function fmtFieldValue(v: JsonValue | undefined, empty = "—"): string {
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
    // GHI CHÚ trong ngoặc đi kèm số tiền trên văn bản (mức quy đổi, lý do khoản thu)
    // — bỏ đi là bỏ đúng chỗ quyết định khoản đó có hợp lệ hay không.
    const note = typeof o.note === "string" && o.note.trim() ? ` (${o.note.trim()})` : "";
    if (amt !== null) {
      const money = [amt.toLocaleString("vi-VN"), fmtCurrency(o.currency)]
        .filter((x) => x !== "").join(" ");
      return (typeof o.period === "string" && o.period ? `${money}/${o.period}` : money) + note;
    }
    if (typeof o.raw === "string" && o.raw.trim()) return o.raw.trim() + note;
    // Vỏ rỗng ({amount:null,...}) hoặc object lạ -> coi như trống.
    return empty;
  }
  return String(v);
}

/** Khóa nhận diện một file trong danh sách tải lên (tên + dung lượng — cùng cách trang
 *  tải lên khử trùng file). Vùng cần kiểm tra gắn với file theo khóa này. */
export function fileKey(f: File): string {
  return f.name + f.size;
}

/** In một trang HTML (báo cáo) qua KHUNG ẨN gắn tạm vào trang hiện tại, rồi gỡ khung sau
 *  khi hộp thoại in đóng. Không dùng `window.open`: cửa sổ phần mềm gốc (pywebview) không
 *  có tab phụ, lời gọi đó bị đẩy sang trình duyệt ngoài với trang trắng. Trả false khi
 *  không dựng được khung. Tiêu đề trang = tên tệp gợi ý khi "Lưu thành PDF". */
export function printHtml(html: string, title: string): boolean {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  // Cỡ 0 chứ không `visibility:hidden` — khung bị ẩn hẳn thì Chromium in ra trang trắng.
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  if (!doc || !win) {
    frame.remove();
    return false;
  }
  doc.open();
  doc.write(html);
  doc.title = title;
  doc.close();
  let removed = false;
  const cleanup = () => {
    if (removed) return;
    removed = true;
    frame.remove();
  };
  win.addEventListener("afterprint", () => setTimeout(cleanup, 0));
  // Chờ ảnh/phông trong báo cáo dựng xong rồi mới mở hộp thoại in.
  setTimeout(() => {
    win.focus();
    win.print();
    // Phòng khi môi trường không bắn `afterprint`: khung 0×0 vô hại, gỡ sau 1 phút.
    setTimeout(cleanup, 60_000);
  }, 350);
  return true;
}
