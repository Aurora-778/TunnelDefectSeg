@echo off
chcp 65001 > nul
echo ============================================================
echo  ResNet50 FCN fixed 6-class resume training
echo ============================================================
echo  EXP_NAME=ResNet50_FCN_6cls_fixed
echo  AUTO_RESUME=1
echo  Final report: Unified Baseline Evaluation Report
echo ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); [Console]::InputEncoding=[System.Text.UTF8Encoding]::new(); $OutputEncoding=[System.Text.UTF8Encoding]::new(); & 'C:\Users\26822\Downloads\data\run_train_resnet50_fixed_resume_visible.ps1'"

echo.
echo Training finished.
echo Final report: C:\Users\26822\Downloads\data\experiments\ResNet50_FCN_6cls_fixed\eval_report.txt
echo Report style: Unified Baseline Evaluation Report
pause
