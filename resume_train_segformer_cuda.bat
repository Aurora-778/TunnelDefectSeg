@echo off
setlocal

title SegFormer CUDA Resume Training
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo SegFormer CUDA resume launcher
echo.
echo Current directory: %CD%
echo Double-click this file to continue from latest.pth.
echo Extra arguments are passed through to run_train_segformer_cuda.bat.
echo ============================================================
echo.

call "%~dp0run_train_segformer_cuda.bat" -ResumeLatest %*
exit /b %ERRORLEVEL%
