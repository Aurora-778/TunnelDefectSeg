$ErrorActionPreference = "Stop"
Set-Location "C:\Users\26822\Downloads\data"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$env:EXP_NAME = "ResNet50_FCN_6cls_fixed"
$env:AUTO_RESUME = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "============================================================"
Write-Host " ResNet50 FCN fixed 6-class resume training"
Write-Host " EXP_NAME=ResNet50_FCN_6cls_fixed"
Write-Host " AUTO_RESUME=1"
Write-Host " Final report: Unified Baseline Evaluation Report"
Write-Host "============================================================"
Write-Host ""

& "D:\users\anaconda3\Scripts\activate.bat" openmmlab | Out-Null

python -u train_resnet50.py 2>&1 | Tee-Object -FilePath train_fixed_resume.log

Write-Host ""
Write-Host "Training finished."
Write-Host "Final report: C:\Users\26822\Downloads\data\experiments\ResNet50_FCN_6cls_fixed\eval_report.txt"
Write-Host "Report style: Unified Baseline Evaluation Report"
Read-Host "Press Enter to close"
