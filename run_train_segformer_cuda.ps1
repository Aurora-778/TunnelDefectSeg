param(
    [string]$PythonExe = 'D:\users\anaconda3\envs\segformer-phase2\python.exe',
    [string]$SegFormerRepoRoot = 'C:\Users\26822\Desktop\隧道病害检测\third_party\SegFormer-master',
    [string]$OutDir = 'experiments\segformer_b1',
    [string]$Pretrained = 'C:\Users\26822\Desktop\隧道病害检测\third_party\SegFormer-master\pretrained\mit_b1.pth',
    [int]$Gpus = 1,
    [switch]$SkipPrepare,
    [switch]$SmokeTest,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Convert-ToPortablePath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).Replace('\', '/')
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $RepoRoot (Join-Path $OutDir 'configs\segformer_b1_6cls.py')
$WorkDir = Join-Path $RepoRoot (Join-Path $OutDir 'runs\segformer_b1_6cls')
$ConfigPathPortable = Convert-ToPortablePath $ConfigPath
$WorkDirPortable = Convert-ToPortablePath $WorkDir

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}
if (-not (Test-Path -LiteralPath $SegFormerRepoRoot)) {
    throw "SegFormer repo not found: $SegFormerRepoRoot"
}

Set-Location $RepoRoot

if (-not $SkipPrepare) {
    $prepareArgs = @(
        'segformer_tools.py',
        '--out-dir', $OutDir,
        '--segformer-repo-root', $SegFormerRepoRoot,
        '--python-executable', $PythonExe
    )
    if (Test-Path -LiteralPath $Pretrained) {
        $prepareArgs += @('--pretrained', $Pretrained)
    } else {
        Write-Warning "Pretrained checkpoint not found, config will be generated without it: $Pretrained"
    }

    Write-Host "[1/2] Preparing SegFormer config and mmseg dataset..."
    if ($DryRun) {
        Write-Host "& '$PythonExe' $($prepareArgs -join ' ')"
    } else {
        & $PythonExe @prepareArgs
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "SegFormer config not found: $ConfigPath"
}

$trainArgs = @(
    'tools/train.py',
    $ConfigPathPortable,
    '--work-dir', $WorkDirPortable,
    '--gpus', $Gpus
)

if ($SmokeTest) {
    $SmokeWorkDir = Join-Path $RepoRoot (Join-Path $OutDir 'runs\smoke_pretrained_cuda')
    $SmokeWorkDirPortable = Convert-ToPortablePath $SmokeWorkDir
    $trainArgs = @(
        'tools/train.py',
        $ConfigPathPortable,
        '--work-dir', $SmokeWorkDirPortable,
        '--gpus', $Gpus,
        '--no-validate',
        '--options',
        'runner.max_iters=1',
        'checkpoint_config.interval=1',
        'data.samples_per_gpu=1',
        'data.workers_per_gpu=0'
    )
}

Write-Host "[2/2] Starting SegFormer CUDA training..."
Write-Host "Python: $PythonExe"
Write-Host "Config: $ConfigPath"
Write-Host "Work dir: $($trainArgs[$trainArgs.IndexOf('--work-dir') + 1])"

if ($DryRun) {
    Write-Host "Set-Location '$SegFormerRepoRoot'"
    Write-Host "& '$PythonExe' $($trainArgs -join ' ')"
    exit 0
}

Set-Location $SegFormerRepoRoot
& $PythonExe @trainArgs
exit $LASTEXITCODE
