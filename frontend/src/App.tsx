import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "./components/Toaster";
import { useT } from "./i18n";

const Home = lazy(() => import("./pages/home"));
const Upload = lazy(() => import("./pages/upload"));
const Review = lazy(() => import("./pages/review"));
const Result = lazy(() => import("./pages/result"));
const Dashboard = lazy(() => import("./pages/dashboard"));
const History = lazy(() => import("./pages/history"));
const Config = lazy(() => import("./pages/config"));
const Admin = lazy(() => import("./pages/admin"));

function Loading() {
  const t = useT();
  return (
    <div className="grid min-h-screen place-items-center text-sm text-slate-500">{t("common.loading")}</div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Toaster />
      <Suspense fallback={<Loading />}>
        <Routes>
          {/* "/" là TRANG CHỦ; luồng kiểm tra bắt đầu ở /kiem-tra. */}
          <Route path="/" element={<Home />} />
          <Route path="/kiem-tra" element={<Upload />} />
          <Route path="/review/:sessionId" element={<Review />} />
          <Route path="/result/:sessionId" element={<Result />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/history" element={<History />} />
          {/* Cấu hình của người dùng — không cần mã quản trị. */}
          <Route path="/cau-hinh" element={<Config />} />
          {/* Quản trị — khu riêng, phải đăng nhập bằng mã quản trị. Hai trang con
              dùng CHUNG component (khác nhau ở phần thân) để không nhân đôi phần
              đăng nhập + vỏ sidebar. */}
          <Route path="/quan-tri" element={<Admin />} />
          <Route path="/quan-tri/he-thong" element={<Admin />} />
          <Route path="/quan-tri/database" element={<Admin />} />
          <Route path="/quan-tri/chi-so" element={<Admin />} />
          <Route path="/quan-tri/kho-luat" element={<Admin />} />
          {/* Đường dẫn cũ /admin trỏ về trang Cấu hình (nơi người dùng thường cần). */}
          <Route path="/admin" element={<Navigate to="/cau-hinh" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
