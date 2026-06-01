$ErrorActionPreference = "Stop"
Set-Location "C:\Users\26822\Downloads\data"

& "D:\users\anaconda3\Scripts\activate.bat" openmmlab | Out-Null

Write-Host "Starting training..."
Write-Host "Logs: train.log"

python -u train_resnet50.py 2>&1 | Tee-Object -FilePath train.log

Write-Host ""
Write-Host "Training finished."
Write-Host "Final report is written to experiments\ResNet50_FCN_6cls\eval_report.txt"
Read-Host "Press Enter to close"
