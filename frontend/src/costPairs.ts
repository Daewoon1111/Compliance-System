/**
 * GHÉP CẶP KHOẢN CHI PHÍ hai bên chi trả — dùng chung cho trang 2 (rà soát) và
 * trang 3 (kết quả).
 *
 * Bảng chi phí là một PHÉP SO: bên tiếp nhận trả khoản nào, người lao động phải
 * trả khoản nào. Xếp mỗi bên theo thứ tự riêng thì hai cột lệch nhau và người đọc
 * phải tự dò tên khoản qua lại; "Tiền dịch vụ" của hai bên có khi cách nhau bốn
 * dòng. Ghép theo KHÁI NIỆM chi phí thì cùng một khoản luôn nằm ngang nhau.
 *
 * Ghép theo NHÃN không được: hai bên gọi khác nhau cho cùng một khoản ("Thị thực
 * visa" ↔ "Tiền visa", "Chi phí khác" ↔ "Hỗ trợ khác"). Ghép theo KHÓA cũng không
 * trực tiếp được vì hậu tố bên chi trả khác nhau và tiền tố thì lúc `tien_`, lúc
 * `chi_phi_`. Rút về một token khái niệm là cách duy nhất ổn định.
 */

/** Thứ tự trình bày — theo trình tự các mục in trên biểu mẫu đăng ký. */
const COST_ORDER = [
  "dich_vu", "di_lai", "dao_tao", "bd_knn", "bd_nn", "quy_htvlnn",
  "kham_suc_khoe", "bhxh", "ho_chieu_lltp", "visa", "khac",
];

/** KHÁI NIỆM chi phí của một field_key, bỏ qua bên chi trả và tiền tố.
 *  Thứ tự các nhánh có ý nghĩa: `chi_phi_bd_knn_nn_*` (bên NLĐ gộp kỹ năng nghề và
 *  ngoại ngữ làm một) phải rơi vào `bd_knn` để bắt cặp với `tien_bd_ky_nang_nghe_*`
 *  của bên tiếp nhận, chứ không rơi vào `bd_nn`. */
export function costConcept(key: string): string {
  const k = key || "";
  if (/dich_vu/.test(k)) return "dich_vu";
  if (/di_lai/.test(k)) return "di_lai";
  if (/dao_tao/.test(k)) return "dao_tao";
  if (/bd_knn|ky_nang_nghe/.test(k)) return "bd_knn";
  if (/ngoai_ngu/.test(k)) return "bd_nn";
  if (/htvlnn/.test(k)) return "quy_htvlnn";
  if (/kham_suc_khoe|kiem_tra_suc_khoe/.test(k)) return "kham_suc_khoe";
  if (/bhxh|bao_hiem_xa_hoi/.test(k)) return "bhxh";
  if (/ho_chieu|lltp|ly_lich_tu_phap/.test(k)) return "ho_chieu_lltp";
  if (/visa|thi_thuc/.test(k)) return "visa";
  if (/khac/.test(k)) return "khac";
  return k;
}

function rank(concept: string): number {
  const i = COST_ORDER.indexOf(concept);
  // Khái niệm lạ (bộ trường của thị trường khác) xếp SAU mọi nhóm đã biết.
  return i < 0 ? COST_ORDER.length : i;
}

/** Xếp một danh sách khoản chi phí theo THỨ TỰ KHÁI NIỆM (giữ nguyên thứ tự gốc
 *  giữa các khoản cùng khái niệm). */
export function sortCosts<T>(items: T[], keyOf: (x: T) => string): T[] {
  return items
    .map((x, i) => ({ x, i, r: rank(costConcept(keyOf(x))) }))
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .map((e) => e.x);
}

/** Xếp hai bên theo thứ tự khái niệm rồi GHÉP THEO CHỈ SỐ — KHÔNG chèn dòng trống.
 *
 *  Đây là lối ghép DUY NHẤT còn dùng, ở cả trang 2 lẫn trang 3. Bản cũ `pairCosts`
 *  ghép theo khái niệm và để ô đối diện trống khi một khoản chỉ có ở một bên — trên
 *  bộ trường thật, bảng thủng bốn ô trắng giữa thân. Ô trống đó không mang tin gì
 *  nhưng mắt vẫn dừng lại ở mỗi lỗ hổng để kiểm xem mình có bỏ sót gì không, nên nó
 *  tốn chú ý mà không trả lại gì; đã gỡ hẳn.
 *
 *  Đánh đổi phải biết: khoản chỉ có ở một bên làm phần còn lại của cột đó LỆCH đi
 *  một dòng, nên từ chỗ lệch trở xuống hai cột không còn là một phép so từng cặp.
 *  Chấp nhận được vì mỗi ô đã in TÊN KHOẢN của chính nó — hai cột đọc độc lập vẫn
 *  đúng, chỉ mất phần đối chiếu theo hàng. */
export function zipCosts<T>(
  left: T[], right: T[], keyOf: (x: T) => string,
): [T | undefined, T | undefined][] {
  const a = sortCosts(left, keyOf);
  const b = sortCosts(right, keyOf);
  return Array.from({ length: Math.max(a.length, b.length) }, (_, i) => [a[i], b[i]]);
}
