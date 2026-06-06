@echo off
setlocal

title Tunnel Defect Web App
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo Tunnel defect live web detector
echo.
echo Double-click this file to start the web app.
echo Default URL: http://127.0.0.1:8000/
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_web_app.ps1" %*

set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
    echo Web app launcher failed. Exit code: %EXIT_CODE%
) else (
    echo Web app launcher finished.
)
echo.
if "%CODEX_NO_PAUSE%"=="1" goto end
pause

:end
exit /b %EXIT_CODE%
