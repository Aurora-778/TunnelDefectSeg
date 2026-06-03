---
title: Windows double-click SegFormer CUDA training launcher
date: 2026-06-03
category: developer-experience
module: SegFormer training workflow
problem_type: developer_experience
component: tooling
severity: medium
applies_when:
  - "A Windows user needs to start CUDA training from File Explorer without typing PowerShell commands"
  - "The training workflow depends on paths containing Chinese characters"
  - "A PowerShell training script must be callable from a .bat wrapper"
tags: [windows, segformer, cuda, training, launcher, powershell, batch]
---

# Windows double-click SegFormer CUDA training launcher

## Context

The SegFormer CUDA training workflow was already functional from PowerShell, but the requested user experience was a script that could be started by double-clicking in Windows Explorer. The repository also needed to survive Windows-specific details: PowerShell execution policy, paths containing Chinese characters, mmcv's sensitivity to backslash escape sequences, and keeping the console open after training so errors remain visible.

The verified local training stack is:

- `D:/users/anaconda3/envs/segformer-phase2/python.exe`
- `torch==1.10.0` with CUDA 11.3
- `mmcv-full==1.4.0`
- SegFormer source at `C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master`
- MiT-B1 pretrained checkpoint at `C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/pretrained/mit_b1.pth`

## Guidance

Use a two-layer launcher:

- A `.bat` file for double-click ergonomics and console lifetime.
- A `.ps1` file for structured PowerShell logic, argument handling, path validation, and command assembly.

The batch wrapper should:

- Change into the repository root with `cd /d "%~dp0"`.
- Use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` so double-click launch does not depend on the user's PowerShell profile or execution policy.
- Keep output text ASCII-safe because CMD can misparse non-ASCII batch files under the active code page.
- Pause only for true double-click use, while allowing `-DryRun` and `-SmokeTest` to exit automatically for verification.

Example:

```bat
@echo off
setlocal

title SegFormer CUDA Training
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_train_segformer_cuda.ps1" %*

set EXIT_CODE=%ERRORLEVEL%
if "%CODEX_NO_PAUSE%"=="1" goto end
if /I "%~1"=="-DryRun" goto end
if /I "%~1"=="-SmokeTest" goto end
pause

:end
exit /b %EXIT_CODE%
```

The PowerShell script should:

- Declare `param(...)` as the first statement in the file.
- Be saved as UTF-8 with BOM when default parameter values contain Chinese paths, so Windows PowerShell 5.1 reads the file correctly.
- Convert config and work-dir arguments to forward-slash paths before passing them to mmcv, avoiding `\U` unicode-escape parsing problems.
- Regenerate the SegFormer config before training unless `-SkipPrepare` is supplied.
- Provide `-DryRun` and `-SmokeTest` modes.

The key path conversion is:

```powershell
function Convert-ToPortablePath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).Replace('\', '/')
}
```

Use the generated launcher for normal training:

```powershell
run_train_segformer_cuda.bat
```

Use smoke test mode before handing the launcher to a user:

```powershell
run_train_segformer_cuda.bat -SmokeTest
```

## Why This Matters

Training can be technically correct but still hard to use if the entrypoint assumes terminal fluency. A double-click launcher removes that friction, but Windows batch and PowerShell encoding rules can silently break paths with Chinese characters. Keeping the `.bat` ASCII-safe and the `.ps1` UTF-8 BOM encoded makes the workflow robust for File Explorer launch.

Forward-slash paths matter because mmcv/yapf config dumping treats backslash sequences like `C:\Users` as Python string escapes. Passing `C:/Users/...` avoids those parser failures without changing the actual Windows path.

Smoke test support matters because full SegFormer training is long. A one-iteration smoke test proves the important pieces quickly: config generation, pretrained checkpoint loading, CUDA visibility, dataset loading, forward/backward training, and checkpoint writing.

## When to Apply

- Use this pattern when a Windows user asks for a training, evaluation, or demo command that can be launched by double-clicking.
- Use it when scripts need to pass Chinese paths from CMD into Windows PowerShell.
- Use it when a long training workflow needs a quick `-SmokeTest` path for verification.
- Use it when generated mmcv configs or command-line arguments contain Windows paths.

## Examples

The repository's production entrypoint is:

```text
run_train_segformer_cuda.bat
```

It wraps:

```text
run_train_segformer_cuda.ps1
```

The PowerShell launcher regenerates the mmseg dataset/config with:

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' segformer_tools.py --out-dir experiments/segformer_b1 --segformer-repo-root 'C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master' --python-executable 'D:/users/anaconda3/envs/segformer-phase2/python.exe' --pretrained 'C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/pretrained/mit_b1.pth'
```

Then it starts training from the SegFormer source tree:

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' tools/train.py 'C:/Users/26822/Downloads/data/experiments/segformer_b1/configs/segformer_b1_6cls.py' --work-dir 'C:/Users/26822/Downloads/data/experiments/segformer_b1/runs/segformer_b1_6cls' --gpus 1
```

The verified smoke test showed:

- CUDA available on the RTX 3060 Laptop GPU.
- The MiT-B1 pretrained checkpoint loaded from the local `mit_b1.pth`.
- The ImageNet classification head keys were ignored as expected.
- A checkpoint was saved after one iteration.

## Related

- `run_train_segformer_cuda.bat`
- `run_train_segformer_cuda.ps1`
- `segformer_tools.py`
- `AGENTS.md`
