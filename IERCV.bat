@echo off
rem IERCV - mo he thong nhu mot phan mem tren may tinh (cua so rieng, khong phai trinh duyet).
rem Dung trong thu muc du an. Ban portable (USB) co IERCV.bat rieng do build_portable.py tao.
cd /d "%~dp0"
if not exist "frontend\dist\index.html" (
  echo Chua co giao dien da build - dang build lan dau...
  call npm run build:ui || (echo Build giao dien that bai. & pause & exit /b 1)
)
rem Uu tien Python cua moi truong ao backend\.venv (README huong dan cai thu vien vao do).
if exist "backend\.venv\Scripts\pythonw.exe" (
  start "" "backend\.venv\Scripts\pythonw.exe" desktop\launcher.py
  exit /b 0
)
where pythonw >nul 2>nul
if errorlevel 1 (
  python desktop\launcher.py
) else (
  start "" pythonw desktop\launcher.py
)
