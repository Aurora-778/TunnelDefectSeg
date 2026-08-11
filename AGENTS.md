# AGENTS.md

## Project Context

This repository is a tunnel defect segmentation workspace. It contains the original ResNet-style training/demo code plus a SegFormer preparation path for a 6-class mmsegmentation dataset.

Main project lines:

- Keep the web demo usable for drag-and-drop tunnel defect inspection.
- Prepare and train a SegFormer B1 backbone for better mask quality.
- Preserve adaptive fusion / selected-mask outputs for comparison with the original model.
- Maintain the robot spatiotemporal monitoring layer for route-level inspection reports.

## Current Handoff Status

- Latest synced commit: `f9adcde feat: add robot spatiotemporal monitoring` on `main` / `origin/main`.
- The robot spatiotemporal monitoring plan is completed:
  `docs/plans/2026-06-25-001-feat-robot-spatiotemporal-defect-monitoring-plan.md`.
- The latest focused validation passed:

```powershell
python -m pytest --rootdir . tests/test_robot_sequence.py tests/test_spatiotemporal_monitoring.py tests/test_robot_inspection_report.py tests/test_web_app.py tests/test_docs_artifact_contract.py tests/test_inspection_report.py tests/test_run_confidence_risk_spatial_mapping.py -q -p no:cacheprovider
```

Expected result: `43 passed`.

Recent safe cleanup removed only caches/logs/upload leftovers:

- `.pytest_cache`
- `__pycache__`
- `tests/__pycache__`
- `web_app_*.log`
- `bash.exe.stackdump`
- `*.baiduyun.uploading.cfg`

Do not treat model weights, dataset shards, codegraph indexes, or experiment folders as disposable without explicit user approval.

## Important Paths

- Repo root: `C:/Users/26822/Downloads/data`
- SegFormer source: `C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master`
- CUDA SegFormer env: `D:/users/anaconda3/envs/segformer-phase2/python.exe`
- Current SegFormer export: `experiments/segformer_b1`
- Generated config: `experiments/segformer_b1/configs/segformer_b1_6cls.py`
- Train launcher: `experiments/segformer_b1/train_segformer_b1.ps1`
- Test launcher: `experiments/segformer_b1/test_segformer_b1.ps1`
- Robot sequence module: `robot_sequence.py`
- Robot spatiotemporal module: `spatiotemporal_monitoring.py`
- Robot route report module: `robot_inspection_report.py`
- Robot route Web asset: `web_demo/assets/robot_route_report.json`
- Robot monitoring competition note: `docs/competition/robot-spatiotemporal-monitoring.md`

## Environment Notes

Use `segformer-phase2` for the old SegFormer/mmsegmentation code.

Verified CUDA stack:

- `torch==1.10.0`
- `torchvision==0.11.1`
- CUDA runtime `11.3`
- `mmcv-full==1.4.0`
- `mmsegmentation==0.11.0` from the desktop SegFormer source tree
- GPU: NVIDIA GeForce RTX 3060 Laptop GPU

The desktop SegFormer source originally rejects `mmcv > 1.3.0`. It has been locally patched at:

`C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/mmseg/__init__.py`

with:

```python
MMCV_MAX = '1.4.0'
```

This is required because the available Windows CUDA wheel is `mmcv-full 1.4.0` for `cu113/torch1.10.0`.

The SegFormer decode head also originally hardcoded `SyncBN`, which fails on Windows single-GPU non-distributed training. It has been locally patched at:

`C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/mmseg/models/decode_heads/segformer_head.py`

with `norm_cfg=self.norm_cfg` inside `linear_fuse`, so the generated config can use `BN`.

The old mmseg evaluation code also used removed NumPy aliases. It has been locally patched at:

`C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/mmseg/core/evaluation/metrics.py`

with `dtype=np.float64` instead of `dtype=np.float`, so validation mIoU evaluation works on NumPy 1.24+.

The mmcv text logger can crash after validation when `time` exists but `data_time` is absent from the log buffer. It has been locally patched at:

`D:/users/anaconda3/envs/segformer-phase2/lib/site-packages/mmcv/runner/hooks/logger/text.py`

to print `data_time` only when that key exists, preventing `KeyError: 'data_time'` after validation.

## Domestic Mirrors

Prefer domestic mirrors for installs:

```powershell
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <packages>
pip install -i http://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com <packages>
conda install -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main --override-channels <packages>
```

Known good CUDA PyTorch install command:

```powershell
& 'D:/users/anaconda3/Scripts/conda.exe' install -n segformer-phase2 -y pytorch==1.10.0 torchvision==0.11.1 cudatoolkit=11.3 -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main --override-channels
```

Known good mmcv wheel command:

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --force-reinstall mmcv-full==1.4.0 -f https://download.openmmlab.com/mmcv/dist/cu113/torch1.10.0/index.html
```

## SegFormer Workflow

Regenerate the mmseg dataset/config/launchers:

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' segformer_tools.py --out-dir experiments/segformer_b1 --segformer-repo-root 'C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master' --python-executable 'D:/users/anaconda3/envs/segformer-phase2/python.exe'
```

Run training:

```powershell
Double-click `run_train_segformer_cuda.bat` from Windows Explorer.
```

Resume training from the current checkpoint:

```powershell
Double-click `resume_train_segformer_cuda.bat` from Windows Explorer.
```

PowerShell equivalent:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1'
```

Resume equivalent:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1' -ResumeLatest
```

Smoke-test the training entrypoint:

```powershell
run_train_segformer_cuda.bat -SmokeTest
```

or:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1' -SmokeTest
```

The generated launcher can also be run directly:

```powershell
& 'C:/Users/26822/Downloads/data/experiments/segformer_b1/train_segformer_b1.ps1'
```

Run evaluation after a checkpoint exists:

```powershell
& 'C:/Users/26822/Downloads/data/experiments/segformer_b1/test_segformer_b1.ps1' -Checkpoint 'C:/path/to/checkpoint.pth'
```

## Verification Commands

Check CUDA environment:

```powershell
@'
import torch, torchvision, cv2, mmcv, timm
from mmcv.ops import CrissCrossAttention
print(torch.__version__, torch.version.cuda, torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda')
print(torchvision.__version__, cv2.__version__, mmcv.__version__, timm.__version__)
'@ | & 'D:/users/anaconda3/envs/segformer-phase2/python.exe' -
```

Run focused tests:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_segformer_tools.py
```

Run robot monitoring / Web / docs focused tests:

```powershell
python -m pytest --rootdir . tests/test_robot_sequence.py tests/test_spatiotemporal_monitoring.py tests/test_robot_inspection_report.py tests/test_web_app.py tests/test_docs_artifact_contract.py tests/test_inspection_report.py tests/test_run_confidence_risk_spatial_mapping.py -q -p no:cacheprovider
```

Check the Web demo script syntax without leaving cache files:

```powershell
$html = Get-Content web_demo/index.html -Raw
$matches = [regex]::Matches($html, '(?s)<script>(.*?)</script>')
$script = ($matches | ForEach-Object { $_.Groups[1].Value }) -join "`n"
$tmp = Join-Path $env:TEMP 'web_demo_index_check.js'
[System.IO.File]::WriteAllText($tmp, $script, [System.Text.UTF8Encoding]::new($false))
node --check $tmp
Remove-Item -LiteralPath $tmp -Force
```

## Known Pitfalls

- Keep Windows paths in generated mmseg configs as forward-slash paths (`C:/...`). Backslashes such as `C:\Users` can break mmcv/yapf config dumping because of `\U` escape parsing.
- Do not rely on default `python` for SegFormer launchers; explicitly use `D:/users/anaconda3/envs/segformer-phase2/python.exe`.
- TensorBoard is disabled in the generated SegFormer config to avoid extra Windows/Python dependency friction. Text logging remains enabled.
- The generated SegFormer config uses `BN`, not `SyncBN`, for Windows single-GPU training.
- The external SegFormer source has a NumPy 1.24 compatibility patch for evaluation metrics (`np.float` -> `np.float64`).
- The local mmcv text logger has a validation-log compatibility patch for missing `data_time`.
- `pretrained/mit_b1.pth` is present under the SegFormer source tree and has been verified with `torch.load`. The generated config can use it with `--pretrained C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/pretrained/mit_b1.pth`.
- The existing web app may already be running at `http://127.0.0.1:8000/`.
- Web live detection must use the same SegFormer source as the static demo when showing SegFormer probability TTA results. Keep `run_web_app.ps1` defaulted to `D:/users/anaconda3/envs/segformer-phase2/python.exe` and pass `--model-source segformer`; check `/api/health` for `model_source: segformer` after launch.
- Enhancement evidence representative examples should prefer non-empty defect foreground cases. Empty stable masks can be used as fallback, but should not be the primary `stable_fused` patent/demo example.
- Uncertainty-to-error evidence has two separate thresholds. `pixel_high_uncertainty_threshold` marks high-uncertainty pixels and is currently `0.35`; `review_fraction_threshold` counts samples whose defect region has enough high-uncertainty pixels and is currently `0.5`. Do not describe both as a single `high_uncertainty_threshold`.
- Robot route reports use a strict claim guard. Same-run changes must remain `apparent-change-evidence`; only comparable cross-cycle evidence or manually verified evidence may be described as `suspected-growth`.
- Keep `comparability_status`, `claim_level`, and `measurement_basis` in route-level outputs. These fields are the boundary between image evidence and engineering claims.
- `mileage=0` is valid route metadata and must not be treated as missing.
- `report_path` must be written into the exported route report JSON before the file is saved.
- The Web robot-route panel must support string mileage such as `K1+002` as well as numeric meter values.
- `1/`, `2/`, `3/`, `4/`, and `5/` contain image/label dataset shards, not empty temporary folders.
- `experiments/segformer_b1`, the ResNet experiment folders, `best_model.pth`, and `resnet50_caffe-788b5fa3.pth` are large, but preserve them unless the user explicitly approves deleting training artifacts or old baselines.
- `.codegraph/` and `.codebase-memory/` are local code intelligence indexes. They are ignored by Git, but do not remove them during routine cleanup unless index rebuild is acceptable.

## Engineering Working Principles

- When developing features, apply razor-law simplicity: choose the smallest coherent implementation that satisfies the requirement, avoid unnecessary abstraction, and cut scope that does not directly serve the current goal.
- When testing and accepting work, apply Murphy's law: assume likely failure modes will happen, and verify invalid input, missing files, stale artifacts, path issues, dependency absence, and degraded states.
- Prefer high cohesion and low coupling: keep modules focused, avoid spreading one contract across unrelated files, and do not make Web, pipeline, model inference, and reporting depend on each other unless the task explicitly requires it.
- Follow established best practices from the local codebase first, then general engineering best practices; do not invent a heavier pattern when the existing project style is enough.
- Ask "What would an OpenAI/Anthropic engineer do here?": favor clear boundaries, explicit contracts, robust tests, truthful claims, and maintainable small changes over clever but fragile implementations.
- Use adversarial multi-agent-style review even when working alone: actively challenge the design, look for counterexamples, and test whether another reviewer could break the change.
- Treat stable lessons as durable memory: once a pattern repeatedly proves useful, record it in `AGENTS.md`, project docs, or available long-term memory so future work does not relearn it.
- For important designs or reviews, use two-agent challenge thinking: one side proposes the simplest viable solution, the other attacks hidden risks, overengineering, missing tests, and claim inflation before accepting the result.

## Repository Rules

- Use `rg` / `rg --files` for search.
- Use `apply_patch` for manual file edits.
- Do not revert user changes.
- After each meaningful work chunk, commit and push to `origin/main` unless the user says otherwise.
- After each project modification, the final summary should include a review prompt tailored to that exact change, so the user can immediately request a focused review of the new work.
- After each review, also generate a repair prompt based on the review findings, so the next work cycle can fix the identified issues directly.
- Treat the default project loop as: implement change -> summarize and provide review prompt -> review -> provide repair prompt -> repair.
- Look in `docs/solutions/` for reusable solved-problem notes before re-solving workflow, tooling, environment, or training-entrypoint issues.
- After each `ce-compound` run, also persist stable reusable conclusions into long-term memory when the environment provides a memory mechanism; if no memory tool is available, record the durable rule in `AGENTS.md` or the relevant project knowledge file.

## Long-running Tool Polling

- For empty-stdin long-running asynchronous work, use `yield_time_ms >= 180000`; prefer `300000` when no intermediate output is needed.
- Use `functions.wait` with `yield_time_ms >= 180000` for long-running cells or sub-agents.
- Set the outer `functions.exec` `@exec` yield time at least 30000 ms longer than the longest nested wait so the outer cell does not yield first.
- Do not apply long waits to non-empty `write_stdin` calls that send interactive input.
- These tools return early when the process or cell completes; do not wake the model merely to report that work is still running.
