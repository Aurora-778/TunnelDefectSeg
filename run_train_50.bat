@echo off
chcp 65001 > nul
cd /d C:\Users\26822\Downloads\data

call D:\users\anaconda3\Scripts\activate.bat openmmlab

set "LOG=train_50.log"
set "ERR=train_50.err.log"
if exist "%LOG%" del "%LOG%"
if exist "%ERR%" del "%ERR%"

python -u -c "import train_resnet50 as m; m.Config.EPOCHS=50; m.Config.EXP_NAME='ResNet50_FCN_6cls_50ep'; m.Config.SAVE_DIR=r'C:\Users\26822\Downloads\data\experiments\ResNet50_FCN_6cls_50ep'; import os; os.makedirs(m.Config.SAVE_DIR, exist_ok=True); m.main()" 1> "%LOG%" 2> "%ERR%"

pause
