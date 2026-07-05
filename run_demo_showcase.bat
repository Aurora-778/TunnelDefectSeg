@echo off
setlocal
cd /d "%~dp0"
python scripts\run_demo_showcase.py %*
if errorlevel 1 (
  echo.
  echo Demo showcase failed. Check the error message above.
  pause
  exit /b 1
)
echo.
echo Demo showcase finished. Open http://127.0.0.1:8000/#video-analysis after starting web_app.py.
pause
