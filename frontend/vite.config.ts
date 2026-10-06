import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // strictPort: 5173 bận thì DỪNG kèm thông báo, thay vì lặng lẽ nhảy sang 5174.
  // Cổng trôi là một origin khác dưới mắt CORS, và triệu chứng người dùng nhận được
  // chỉ là "Failed to fetch" ở màn hình đầu tiên — tốn nhiều thời gian truy sai chỗ
  // hơn hẳn so với việc báo thẳng "cổng đang bận".
  server: { port: 5173, strictPort: true },
})
