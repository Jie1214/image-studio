@echo off
rem ============================================================
rem  Image Studio launcher  (ASCII only: cmd codepage safe)
rem ============================================================
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal

echo ============================================================
echo   Image Studio  (tu xiang gong fang - pi liang ya suo)
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] .venv not found - creating it with py -3.13 ...
  py -3.13 -m venv .venv --system-site-packages
  if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not create .venv. Is Python 3.13 installed? Try: py -3.13 -m venv .venv --system-site-packages
    echo.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import PIL" 2>nul
if not "%ERRORLEVEL%"=="0" (
  echo [INFO] Pillow not found - installing from the default index ...
  ".venv\Scripts\python.exe" -m pip install --quiet pillow
  ".venv\Scripts\python.exe" -c "import PIL" 2>nul
  if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] Pillow is required. Run: .venv\Scripts\python.exe -m pip install pillow
    echo.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" run_server.py %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo [ERROR] Failed to start, exit code %RC%. Read the message above.
  echo.
  pause
)
endlocal
exit /b %RC%
