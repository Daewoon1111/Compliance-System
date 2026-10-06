/**
 * BỘ BIỂU TƯỢNG — dựng từ **Boxicons** (gói `boxicons`, giấy phép MIT).
 *
 * Path lấy nguyên văn từ `node_modules/boxicons/svg/regular/*.svg` rồi nội tuyến vào
 * đây thay vì nạp font/CSS của Boxicons: hệ thống chạy offline (LLM cục bộ) nên
 * không nên phụ thuộc CDN, và nội tuyến thì icon tô theo `currentColor` — tự đổi
 * màu theo nền sáng/tối mà không cần khai lại bảng màu.
 *
 * Mỗi icon ghi rõ tên file gốc để sau này tra ngược được.
 */
type IconProps = { className?: string };

const BASE = "h-5 w-5";

/** Vỏ chung: viewBox 24×24 + fill currentColor (đúng quy ước Boxicons). */
function Bx({ className = BASE, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      {children}
    </svg>
  );
}

/** bx-menu */
export function IconMenu({ className }: IconProps) {
  return <Bx className={className}><path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z" /></Bx>;
}

/** bx-sun */
export function IconSun({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M6.993 12c0 2.761 2.246 5.007 5.007 5.007s5.007-2.246 5.007-5.007S14.761 6.993 12 6.993 6.993 9.239 6.993 12zM12 8.993c1.658 0 3.007 1.349 3.007 3.007S13.658 15.007 12 15.007 8.993 13.658 8.993 12 10.342 8.993 12 8.993zM10.998 19h2v3h-2zm0-17h2v3h-2zm-9 9h3v2h-3zm17 0h3v2h-3zM4.219 18.363l2.12-2.122 1.415 1.414-2.12 2.122zM16.24 6.344l2.122-2.122 1.414 1.414-2.122 2.122zM6.342 7.759 4.22 5.637l1.415-1.414 2.12 2.122zm13.434 10.605-1.414 1.414-2.122-2.122 1.414-1.414z" />
    </Bx>
  );
}

/** bx-moon */
export function IconMoon({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M20.742 13.045a8.088 8.088 0 0 1-2.077.271c-2.135 0-4.14-.83-5.646-2.336a8.025 8.025 0 0 1-2.064-7.723A1 1 0 0 0 9.73 2.034a10.014 10.014 0 0 0-4.489 2.582c-3.898 3.898-3.898 10.243 0 14.143a9.937 9.937 0 0 0 7.072 2.93 9.93 9.93 0 0 0 7.07-2.929 10.007 10.007 0 0 0 2.583-4.491 1.001 1.001 0 0 0-1.224-1.224zm-2.772 4.301a7.947 7.947 0 0 1-5.656 2.343 7.953 7.953 0 0 1-5.658-2.344c-3.118-3.119-3.118-8.195 0-11.314a7.923 7.923 0 0 1 2.06-1.483 10.027 10.027 0 0 0 2.89 7.848 9.972 9.972 0 0 0 7.848 2.891 8.036 8.036 0 0 1-1.484 2.059z" />
    </Bx>
  );
}

/** bx-globe */
export function IconLanguage({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M12 2C6.486 2 2 6.486 2 12s4.486 10 10 10 10-4.486 10-10S17.514 2 12 2zm7.931 9h-2.764a14.67 14.67 0 0 0-1.792-6.243A8.013 8.013 0 0 1 19.931 11zM12.53 4.027c1.035 1.364 2.427 3.78 2.627 6.973H9.03c.139-2.596.994-5.028 2.451-6.974.172-.01.344-.026.519-.026.179 0 .354.016.53.027zm-3.842.7C7.704 6.618 7.136 8.762 7.03 11H4.069a8.013 8.013 0 0 1 4.619-6.273zM4.069 13h2.974c.136 2.379.665 4.478 1.556 6.23A8.01 8.01 0 0 1 4.069 13zm7.381 6.973C10.049 18.275 9.222 15.896 9.041 13h6.113c-.208 2.773-1.117 5.196-2.603 6.972-.182.012-.364.028-.551.028-.186 0-.367-.016-.55-.027zm4.011-.772c.955-1.794 1.538-3.901 1.691-6.201h2.778a8.005 8.005 0 0 1-4.469 6.201z" />
    </Bx>
  );
}

/** bx-bell */
export function IconBell({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M19 13.586V10c0-3.217-2.185-5.927-5.145-6.742C13.562 2.52 12.846 2 12 2s-1.562.52-1.855 1.258C7.185 4.074 5 6.783 5 10v3.586l-1.707 1.707A.996.996 0 0 0 3 16v2a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-2a.996.996 0 0 0-.293-.707L19 13.586zM19 17H5v-.586l1.707-1.707A.996.996 0 0 0 7 14v-4c0-2.757 2.243-5 5-5s5 2.243 5 5v4c0 .266.105.52.293.707L19 16.414V17zm-7 5a2.98 2.98 0 0 0 2.818-2H9.182A2.98 2.98 0 0 0 12 22z" />
    </Bx>
  );
}

/** bx-home-alt — Trang chủ */
export function IconHome({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M5 22h14a2 2 0 0 0 2-2v-9a1 1 0 0 0-.29-.71l-8-8a1 1 0 0 0-1.41 0l-8 8A1 1 0 0 0 3 11v9a2 2 0 0 0 2 2zm5-2v-5h4v5zm-5-8.59 7-7 7 7V20h-3v-5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v5H5z" />
    </Bx>
  );
}

/** bx-check-shield — Kiểm tra hồ sơ */
export function IconCheckShield({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M20.995 6.9a.998.998 0 0 0-.548-.795l-8-4a1 1 0 0 0-.895 0l-8 4a1.002 1.002 0 0 0-.547.795c-.011.107-.961 10.767 8.589 15.014a.987.987 0 0 0 .812 0c9.55-4.247 8.6-14.906 8.589-15.014zM12 19.897C5.231 16.625 4.911 9.642 4.966 7.635L12 4.118l7.029 3.515c.037 1.989-.328 9.018-7.029 12.264z" />
      <path d="m11 12.586-2.293-2.293-1.414 1.414L11 15.414l5.707-5.707-1.414-1.414z" />
    </Bx>
  );
}

/** bx-bar-chart-alt-2 — Thống kê */
export function IconChart({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M20 7h-4V4c0-1.103-.897-2-2-2h-4c-1.103 0-2 .897-2 2v5H4c-1.103 0-2 .897-2 2v9a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V9c0-1.103-.897-2-2-2zM4 11h4v8H4v-8zm6-1V4h4v15h-4v-9zm10 9h-4V9h4v10z" />
    </Bx>
  );
}

/** bx-history — Lịch sử */
export function IconHistory({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M12 8v5h5v-2h-3V8z" />
      <path d="M21.292 8.497a8.957 8.957 0 0 0-1.928-2.862 9.004 9.004 0 0 0-4.55-2.452 9.09 9.09 0 0 0-3.626 0 8.965 8.965 0 0 0-4.552 2.453 9.048 9.048 0 0 0-1.928 2.86A8.963 8.963 0 0 0 4 12l.001.025H2L5 16l3-3.975H6.001L6 12a6.957 6.957 0 0 1 1.195-3.913 7.066 7.066 0 0 1 1.891-1.892 7.034 7.034 0 0 1 2.503-1.054 7.003 7.003 0 0 1 8.269 5.445 7.117 7.117 0 0 1 0 2.824 6.936 6.936 0 0 1-1.054 2.503c-.25.371-.537.72-.854 1.036a7.058 7.058 0 0 1-2.225 1.501 6.98 6.98 0 0 1-1.313.408 7.117 7.117 0 0 1-2.823 0 6.957 6.957 0 0 1-2.501-1.053 7.066 7.066 0 0 1-1.037-.855l-1.414 1.414A8.985 8.985 0 0 0 13 21a9.05 9.05 0 0 0 3.503-.707 9.009 9.009 0 0 0 3.959-3.26A8.968 8.968 0 0 0 22 12a8.928 8.928 0 0 0-.708-3.503z" />
    </Bx>
  );
}

/** bx-cog — Cấu hình */
export function IconCog({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M12 16c2.206 0 4-1.794 4-4s-1.794-4-4-4-4 1.794-4 4 1.794 4 4 4zm0-6c1.084 0 2 .916 2 2s-.916 2-2 2-2-.916-2-2 .916-2 2-2z" />
      <path d="m2.845 16.136 1 1.73c.531.917 1.809 1.261 2.73.73l.529-.306A8.1 8.1 0 0 0 9 19.402V20c0 1.103.897 2 2 2h2c1.103 0 2-.897 2-2v-.598a8.132 8.132 0 0 0 1.896-1.111l.529.306c.923.53 2.198.188 2.731-.731l.999-1.729a2.001 2.001 0 0 0-.731-2.732l-.505-.292a7.718 7.718 0 0 0 0-2.224l.505-.292a2.002 2.002 0 0 0 .731-2.732l-.999-1.729c-.531-.92-1.808-1.265-2.731-.732l-.529.306A8.1 8.1 0 0 0 15 4.598V4c0-1.103-.897-2-2-2h-2c-1.103 0-2 .897-2 2v.598a8.132 8.132 0 0 0-1.896 1.111l-.529-.306c-.924-.531-2.2-.187-2.731.732l-.999 1.729a2.001 2.001 0 0 0 .731 2.732l.505.292a7.683 7.683 0 0 0 0 2.223l-.505.292a2.003 2.003 0 0 0-.731 2.733zm3.326-2.758A5.703 5.703 0 0 1 6 12c0-.462.058-.926.17-1.378a.999.999 0 0 0-.47-1.108l-1.123-.65.998-1.729 1.145.662a.997.997 0 0 0 1.188-.142 6.071 6.071 0 0 1 2.384-1.399A1 1 0 0 0 11 5.3V4h2v1.3a1 1 0 0 0 .708.956 6.083 6.083 0 0 1 2.384 1.399.999.999 0 0 0 1.188.142l1.144-.661 1 1.729-1.124.649a1 1 0 0 0-.47 1.108c.112.452.17.916.17 1.378 0 .461-.058.925-.171 1.378a1 1 0 0 0 .471 1.108l1.123.649-.998 1.729-1.145-.661a.996.996 0 0 0-1.188.142 6.071 6.071 0 0 1-2.384 1.399A1 1 0 0 0 13 18.7l.002 1.3H11v-1.3a1 1 0 0 0-.708-.956 6.083 6.083 0 0 1-2.384-1.399.992.992 0 0 0-1.188-.141l-1.144.662-1-1.729 1.124-.651a1 1 0 0 0 .471-1.108z" />
    </Bx>
  );
}

/** bx-shield-quarter — Quản trị hệ thống */
export function IconShield({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M20.995 6.9a.998.998 0 0 0-.548-.795l-8-4a1 1 0 0 0-.895 0l-8 4a1.002 1.002 0 0 0-.547.795c-.011.107-.961 10.767 8.589 15.014a.987.987 0 0 0 .812 0c9.55-4.247 8.6-14.906 8.589-15.014zM12 19.897V12H5.51a15.473 15.473 0 0 1-.544-4.365L12 4.118V12h6.46c-.759 2.74-2.498 5.979-6.46 7.897z" />
    </Bx>
  );
}

/** bx-data — Kiểm tra database */
export function IconDatabase({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M20 17V7c0-2.168-3.663-4-8-4S4 4.832 4 7v10c0 2.168 3.663 4 8 4s8-1.832 8-4zM12 5c3.691 0 5.931 1.507 6 1.994C17.931 7.493 15.691 9 12 9S6.069 7.493 6 7.006C6.069 6.507 8.309 5 12 5zM6 9.607C7.479 10.454 9.637 11 12 11s4.521-.546 6-1.393v2.387c-.069.499-2.309 2.006-6 2.006s-5.931-1.507-6-2V9.607zM6 17v-2.393C7.479 15.454 9.637 16 12 16s4.521-.546 6-1.393v2.387c-.069.499-2.309 2.006-6 2.006s-5.931-1.507-6-2z" />
    </Bx>
  );
}

/** bx-log-out — Đăng xuất quản trị */
export function IconLogout({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M16 13v-2H7V8l-5 4 5 4v-3z" />
      <path d="M20 3h-9c-1.103 0-2 .897-2 2v4h2V5h9v14h-9v-4H9v4c0 1.103.897 2 2 2h9c1.103 0 2-.897 2-2V5c0-1.103-.897-2-2-2z" />
    </Bx>
  );
}

/** bx-note — Tạo cấu hình mới (cố ý KHÔNG dùng icon dấu cộng) */
export function IconNote({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M19 3H5c-1.103 0-2 .897-2 2v14c0 1.103.897 2 2 2h8a.996.996 0 0 0 .707-.293l7-7a.997.997 0 0 0 .196-.293c.014-.03.022-.061.033-.093a.991.991 0 0 0 .051-.259c.002-.021.013-.041.013-.062V5c0-1.103-.897-2-2-2zM5 5h14v7h-6a1 1 0 0 0-1 1v6H5V5zm9 12.586V14h3.586L14 17.586z" />
    </Bx>
  );
}

/** bx-cloud-upload — vùng tải file cấu hình lên */
export function IconUpload({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M13 19v-4h3l-4-5-4 5h3v4z" />
      <path d="M7 19h2v-2H7c-1.654 0-3-1.346-3-3 0-1.404 1.199-2.756 2.673-3.015l.581-.102.192-.558C8.149 8.274 9.895 7 12 7c2.757 0 5 2.243 5 5v1h1c1.103 0 2 .897 2 2s-.897 2-2 2h-3v2h3c2.206 0 4-1.794 4-4a4.01 4.01 0 0 0-3.056-3.888C18.507 7.67 15.56 5 12 5 9.244 5 6.85 6.611 5.757 9.15 3.609 9.792 2 11.82 2 14c0 2.757 2.243 5 5 5z" />
    </Bx>
  );
}

/** bx-edit-alt — nút sửa giá trị trường */
export function IconEdit({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M19.045 7.401c.378-.378.586-.88.586-1.414s-.208-1.036-.586-1.414l-1.586-1.586c-.378-.378-.88-.586-1.414-.586s-1.036.208-1.413.585L4 13.585V18h4.413L19.045 7.401zm-3-3 1.587 1.585-1.59 1.584-1.586-1.585 1.589-1.584zM6 16v-1.585l7.04-7.018 1.586 1.586L7.587 16H6zm-2 4h16v2H4z" />
    </Bx>
  );
}

/** bx-file — nút MỞ văn bản OCR (trang 2). */
export function IconFile({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M19.903 8.586a.997.997 0 0 0-.196-.293l-6-6a.997.997 0 0 0-.293-.196c-.03-.014-.062-.022-.094-.033a.991.991 0 0 0-.259-.051C13.04 2.011 13.021 2 13 2H6c-1.103 0-2 .897-2 2v16c0 1.103.897 2 2 2h12c1.103 0 2-.897 2-2V9c0-.021-.011-.04-.013-.062a.952.952 0 0 0-.051-.259c-.01-.032-.019-.063-.033-.093zM16.586 9H14V6.414L16.586 9zM6 20V4h6v5a1 1 0 0 0 1 1h5l.002 10H6z" />
    </Bx>
  );
}

/** bx-x — nút ĐÓNG (thẻ văn bản OCR). */
export function IconX({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="m16.192 6.344-4.243 4.242-4.242-4.242-1.414 1.414L10.535 12l-4.242 4.242 1.414 1.414 4.242-4.242 4.243 4.242 1.414-1.414L13.364 12l4.242-4.242z" />
    </Bx>
  );
}

/** bx-toggle-left / bx-toggle-right — công tắc Áp dụng cấu hình */
export function IconToggle({ on, className }: IconProps & { on: boolean }) {
  return on ? (
    <Bx className={className}>
      <path d="M16 9c-1.628 0-3 1.372-3 3s1.372 3 3 3 3-1.372 3-3-1.372-3-3-3z" />
      <path d="M16 6H8c-3.296 0-5.982 2.682-6 5.986v.042A6.01 6.01 0 0 0 8 18h8c3.309 0 6-2.691 6-6s-2.691-6-6-6zm0 10H8a4.006 4.006 0 0 1-4-3.99C4.004 9.799 5.798 8 8 8h8c2.206 0 4 1.794 4 4s-1.794 4-4 4z" />
    </Bx>
  ) : (
    <Bx className={className}>
      <path d="M8 9c-1.628 0-3 1.372-3 3s1.372 3 3 3 3-1.372 3-3-1.372-3-3-3z" />
      <path d="M16 6H8c-3.3 0-5.989 2.689-6 6v.016A6.01 6.01 0 0 0 8 18h8a6.01 6.01 0 0 0 6-5.994V12c-.009-3.309-2.699-6-6-6zm0 10H8a4.006 4.006 0 0 1-4-3.99C4.004 9.799 5.798 8 8 8h8c2.202 0 3.996 1.799 4 4.006A4.007 4.007 0 0 1 16 16zm4-3.984.443-.004.557.004h-1z" />
    </Bx>
  );
}

/** bx-error — cảnh báo (tam giác chấm than). Thay ký tự ⚠️. */
export function IconWarning({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M11.001 10h2v5h-2zM11 16h2v2h-2z" />
      <path d="M13.768 4.2C13.42 3.545 12.742 3.138 12 3.138s-1.42.407-1.768 1.063L2.894 18.064a1.986 1.986 0 0 0 .054 1.968A1.984 1.984 0 0 0 4.661 21h14.678c.708 0 1.349-.362 1.714-.968a1.989 1.989 0 0 0 .054-1.968L13.768 4.2zM4.661 19 12 5.137 19.344 19H4.661z" />
    </Bx>
  );
}

/** bx-block — cấm / vi phạm (vòng tròn gạch chéo). Thay ký tự ⛔. */
export function IconBlock({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M12 2C6.486 2 2 6.486 2 12s4.486 10 10 10 10-4.486 10-10S17.514 2 12 2zM4 12c0-1.846.634-3.542 1.688-4.897l11.209 11.209A7.946 7.946 0 0 1 12 20c-4.411 0-8-3.589-8-8zm14.312 4.897L7.103 5.688A7.948 7.948 0 0 1 12 4c4.411 0 8 3.589 8 8a7.954 7.954 0 0 1-1.688 4.897z" />
    </Bx>
  );
}

/** bx-info-circle — thông tin. Thay ký tự ℹ️. */
export function IconInfo({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="M12 2C6.486 2 2 6.486 2 12s4.486 10 10 10 10-4.486 10-10S17.514 2 12 2zm0 18c-4.411 0-8-3.589-8-8s3.589-8 8-8 8 3.589 8 8-3.589 8-8 8z" />
      <path d="M11 11h2v6h-2zm0-4h2v2h-2z" />
    </Bx>
  );
}

/** bx-check — dấu tích. Thay ký tự ✓/✅. */
export function IconCheck({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="m10 15.586-3.293-3.293-1.414 1.414L10 18.414l9.707-9.707-1.414-1.414z" />
    </Bx>
  );
}

/** bx-download — tải xuống. Thay ký tự ⬇. */
export function IconDownload({ className }: IconProps) {
  return (
    <Bx className={className}>
      <path d="m12 16 4-5h-3V4h-2v7H8z" />
      <path d="M20 18H4v-7H2v7c0 1.103.897 2 2 2h16c1.103 0 2-.897 2-2v-7h-2v7z" />
    </Bx>
  );
}

/** bx-chevron-up / -down / -left / -right — mũi tên gập/phân trang. */
export function IconChevronUp({ className }: IconProps) {
  return <Bx className={className}><path d="m6.293 13.293 1.414 1.414L12 10.414l4.293 4.293 1.414-1.414L12 7.586z" /></Bx>;
}
export function IconChevronDown({ className }: IconProps) {
  return <Bx className={className}><path d="M16.293 9.293 12 13.586 7.707 9.293l-1.414 1.414L12 16.414l5.707-5.707z" /></Bx>;
}
export function IconChevronLeft({ className }: IconProps) {
  return <Bx className={className}><path d="M13.293 6.293 7.586 12l5.707 5.707 1.414-1.414L10.414 12l4.293-4.293z" /></Bx>;
}
export function IconChevronRight({ className }: IconProps) {
  return <Bx className={className}><path d="M10.707 17.707 16.414 12l-5.707-5.707-1.414 1.414L13.586 12l-4.293 4.293z" /></Bx>;
}

/**
 * LOGO IERCV — ghép 3 hình: tờ hồ sơ có dòng kẻ (đang đọc) + vòng tròn dấu tích
 * (đã kiểm và đạt) + vòng đo tuân thủ (đối chiếu theo chuẩn).
 */
export function Logo({ className = "h-8 w-8" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        fill="currentColor"
        d="M10 23c0 .553-.447 1-1 1H5c-2.757 0-5-2.243-5-5V5c0-2.757 2.243-5 5-5h8c2.757 0 5 2.243 5 5v2c0 .553-.447 1-1 1s-1-.447-1-1V5c0-1.654-1.346-3-3-3H5C3.346 2 2 3.346 2 5v14c0 1.654 1.346 3 3 3h4c.553 0 1 .447 1 1ZM14 6c0-.553-.447-1-1-1H5c-.553 0-1 .447-1 1s.447 1 1 1h8c.553 0 1-.447 1-1Zm-4 5c0-.553-.447-1-1-1H5c-.553 0-1 .447-1 1s.447 1 1 1h4c.553 0 1-.447 1-1Zm-5 4c-.553 0-1 .447-1 1s.447 1 1 1h2c.553 0 1-.447 1-1s-.447-1-1-1H5Z"
      />
      <path
        className="text-blue-600"
        fill="currentColor"
        d="M24 17c0 3.859-3.141 7-7 7s-7-3.141-7-7 3.141-7 7-7 7 3.141 7 7Zm-2 0c0-2.757-2.243-5-5-5s-5 2.243-5 5 2.243 5 5 5 5-2.243 5-5Zm-3.192-1.241-2.223 2.134a.37.37 0 0 1-.522.002l-1.131-1.108a1 1 0 0 0-1.414.014 1 1 0 0 0 .014 1.414l1.132 1.109c.46.449 1.062.674 1.663.674s1.201-.225 1.653-.671l2.213-2.124a1 1 0 0 0 .029-1.414 1 1 0 0 0-1.414-.03Z"
      />
      <path
        className="text-blue-600"
        fill="currentColor"
        d="M15.5 1.2a3.3 3.3 0 1 0 0 6.6 3.3 3.3 0 0 0 0-6.6Zm0 4.7a1.4 1.4 0 1 1 0-2.8 1.4 1.4 0 0 1 0 2.8Z"
      />
    </svg>
  );
}
