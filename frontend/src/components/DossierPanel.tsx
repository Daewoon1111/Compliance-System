/**
 * DossierPanel + FlagList — dùng chung cho trang Kiểm tra (review) và Kết quả (result).
 * Dùng chung một component để hai trang không trôi thành hai bản gần giống nhau.
 */
import type { DossierAnalysis, InputFlag } from "../types";
import { CARD } from "../ui";
import { useLang, useT } from "../i18n";
import { IconWarning, IconBlock, IconCheck } from "./Icons";

const TH =
  "border-b border-slate-200 bg-white px-3 py-2 text-left text-[13px] font-semibold text-slate-700";

/** Danh sách cảnh báo/lỗi (⚠️/⛔) — tự ẩn khi rỗng.
 *
 *  `onPickField`: có truyền thì cờ nào chỉ đích danh một trường (`f.field`) trở
 *  thành NÚT — bấm là cuộn tới đúng hàng đó trong bảng để sửa. Cảnh báo bảo "hãy sửa
 *  ngày ký" mà người đọc phải tự dò trong bảng vài chục trường thì lời khuyên đó gần
 *  như không được làm theo. */
export function FlagList(
  { flags, onPickField }: { flags: InputFlag[]; onPickField?: (key: string) => void },
) {
  if (!flags?.length) return null;
  return (
    <div className="grid gap-1.5">
      {flags.map((f, i) => {
        const cls =
          "flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-[13px] " +
          (f.level === "error"
            ? "border-red-300 bg-red-50 text-red-800"
            : "border-amber-300 bg-amber-50 text-amber-900");
        const icon = f.level === "error"
          ? <IconBlock className="h-5 w-5 shrink-0 text-red-600" />
          : <IconWarning className="h-5 w-5 shrink-0 text-amber-600" />;
        const key = f.field || "";
        return onPickField && key ? (
          <button key={i} type="button" className={cls + " hover:brightness-95"}
                  onClick={() => onPickField(key)}>
            {icon}<span className="underline decoration-dotted">{f.message}</span>
          </button>
        ) : (
          <div key={i} className={cls}>{icon}<span>{f.message}</span></div>
        );
      })}
    </div>
  );
}

/** Thẻ "Kiểm tra bộ hồ sơ": cờ A3/C1-C4 + bảng vai trò từng tài liệu.
 *  showFlags=false: trang Kết quả đã gom cờ lên khối kết luận chung -> ở đây chỉ
 *  còn bảng vai trò tài liệu, không lặp lại cảnh báo. */
export function DossierPanel({
  dossier, showFlags = true, flat = false,
}: {
  dossier?: DossierAnalysis; showFlags?: boolean;
  /** `flat`: bỏ vỏ thẻ (viền + nền + bóng) để NHÚNG vào một thẻ khác. Trang Kết quả
   *  đặt bảng này ngay dưới "Thông tin hồ sơ" trong CÙNG một thẻ — thẻ lồng trong
   *  thẻ trông như hai khối rời, đúng thứ vừa gộp lại để tránh. */
  flat?: boolean;
}) {
  const t = useT();
  const lang = useLang();
  if (!dossier) return null;
  const flags = showFlags ? dossier.flags || [] : [];
  const roles = dossier.roles || [];
  if (!flags.length && !roles.length) return null;
  return (
    <div className={flat ? "rounded-lg border border-slate-200 p-3" : CARD + " mb-4"}>
      <div className={flat
        ? "mb-2 text-[12px] font-semibold uppercase tracking-wide text-slate-500"
        : "mb-2 text-base font-bold text-slate-800"}>
        {t("ds.title")} ({roles.length} {t("ds.docsUnit")})
      </div>
      {/* BẢNG TÀI LIỆU đứng TRƯỚC danh sách cảnh báo.
          Thẻ này tên là "Kiểm tra bộ hồ sơ" nên câu đầu tiên nó phải trả lời là "bộ
          hồ sơ gồm những file nào" — rồi mới tới "thiếu gì". Đặt cảnh báo (6–8 dòng)
          lên trên sẽ đẩy bảng tài liệu xuống dưới màn hình: người đọc thấy một loạt
          "thiếu thành phần…" mà chưa biết đang có sẵn thành phần nào. */}
      {roles.length ? (
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <th className={TH + " w-3/5"}>{t("ds.colDoc")}</th>
              <th className={TH}>{t("ds.colRole")}</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((r, i) => (
              <tr key={i} className={r.role === "unknown" ? "bg-amber-50" : ""}>
                <td className="border-t border-slate-100 px-3 py-1.5 align-top wrap-break-word text-slate-600">
                  {r.source_file}
                </td>
                <td className="border-t border-slate-100 px-3 py-1.5 align-top font-medium text-slate-700">
                  {/* Nhãn vai trò do backend gửi kèm CẢ HAI bản; báo cáo cũ chưa có
                      `label_en` thì lùi về bản tiếng Việt thay vì hiện khóa trống. */}
                  {(lang === "en" ? r.label_en : r.label) || r.label}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {flags.length ? (
        <div className={roles.length ? "mt-3" : ""}>
          <FlagList flags={flags} />
        </div>
      ) : showFlags ? (
        <div className="mt-2 flex items-center gap-1.5 text-[13px] font-medium text-green-700">
          <IconCheck className="h-4.5 w-4.5 shrink-0" />
          {t("ds.allGood")}
        </div>
      ) : null}
    </div>
  );
}
