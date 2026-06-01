@echo off
chcp 65001 > nul
echo ============================================================
echo  裂缝数据集 MMDetection 训练流程
echo ============================================================

:: ── 1. 激活 conda 环境 ──────────────────────────────────────
set CONDA_ROOT=D:\users\anaconda3
call "%CONDA_ROOT%\Scripts\activate.bat" openmmlab
if errorlevel 1 (
    echo [错误] 激活 conda 环境 openmmlab 失败
    pause
    exit /b 1
)

:: ── 2. 转换数据集 ────────────────────────────────────────────
echo.
echo [步骤 1/3] 转换 PNG mask → COCO JSON ...
python "C:\Users\26822\Downloads\data\convert_to_coco.py"
if errorlevel 1 (
    echo [错误] 数据集转换失败
    pause
    exit /b 1
)
echo [完成] 数据集转换成功

:: ── 3. 验证 COCO JSON（可选）───────────────────────────────
echo.
echo [步骤 2/3] 验证 COCO 标注 ...
python -c "
import json, pathlib
for split in ['train', 'val']:
    p = pathlib.Path(r'C:\Users\26822\Downloads\data\annotations') / f'{split}.json'
    d = json.loads(p.read_text())
    print(f'  {split}: {len(d[\"images\"])} 图像, {len(d[\"annotations\"])} 标注')
print('  验证通过')
"
if errorlevel 1 (
    echo [错误] COCO JSON 验证失败
    pause
    exit /b 1
)

:: ── 4. 查找 mmdetection 目录 ────────────────────────────────
set MMDET_DIR=
for /d %%d in (
    "C:\mmdetection"
    "D:\mmdetection"
    "%USERPROFILE%\mmdetection"
    "C:\Users\26822\mmdetection"
) do (
    if exist "%%~d\tools\train.py" (
        set MMDET_DIR=%%~d
        goto :found_mmdet
    )
)
echo [警告] 未找到 mmdetection 安装目录，请在下方设置 MMDET_DIR
echo.
set /p MMDET_DIR=请输入 mmdetection 目录（含 tools\train.py）:
if not exist "%MMDET_DIR%\tools\train.py" (
    echo [错误] 找不到 %MMDET_DIR%\tools\train.py
    pause
    exit /b 1
)

:found_mmdet
echo [完成] mmdetection 目录: %MMDET_DIR%

:: ── 5. 开始训练 ──────────────────────────────────────────────
echo.
echo [步骤 3/3] 启动 Mask R-CNN 训练 ...
echo   配置文件: C:\Users\26822\Downloads\data\configs\mask-rcnn_crack.py
echo   输出目录: C:\Users\26822\Downloads\data\work_dirs\mask-rcnn_crack
echo.
python "%MMDET_DIR%\tools\train.py" ^
    "C:\Users\26822\Downloads\data\configs\mask-rcnn_crack.py" ^
    --work-dir "C:\Users\26822\Downloads\data\work_dirs\mask-rcnn_crack"

if errorlevel 1 (
    echo [错误] 训练失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  训练完成！检查点保存在:
echo  C:\Users\26822\Downloads\data\work_dirs\mask-rcnn_crack
echo ============================================================
pause
