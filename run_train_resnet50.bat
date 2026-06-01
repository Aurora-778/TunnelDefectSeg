@echo off
setlocal
cd /d C:\Users\26822\Downloads\data

powershell -NoProfile -ExecutionPolicy Bypass -Command "python -u train_resnet50.py 2>&1 | Tee-Object -FilePath train.log"

echo.
echo Training finished.
echo Final report: C:\Users\26822\Downloads\data\experiments\ResNet50_FCN_6cls\eval_report.txt
pause
