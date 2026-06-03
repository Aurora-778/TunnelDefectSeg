@echo off
setlocal

title SegFormer CUDA Training
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo SegFormer CUDA training launcher
echo.
echo Current directory: %CD%
echo Double-click this file to start full training.
echo For smoke test, run from command line:
echo       run_train_segformer_cuda.bat -SmokeTest
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_train_segformer_cuda.ps1" %*

set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
    echo Training script failed. Exit code: %EXIT_CODE%
) else (
    echo Training script finished.
)
echo.
if "%CODEX_NO_PAUSE%"=="1" goto end
if /I "%~1"=="-DryRun" goto end
if /I "%~1"=="-SmokeTest" goto end
pause

:end
exit /b %EXIT_CODE%
