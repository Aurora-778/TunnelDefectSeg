# AGENTS.md

## Project Context

This repository is a tunnel defect segmentation workspace. It contains the original ResNet-style training/demo code plus a SegFormer preparation path for a 6-class mmsegmentation dataset.

Main goals currently in progress:

- Keep the web demo usable for drag-and-drop tunnel defect inspection.
- Prepare and train a SegFormer B1 backbone for better mask quality.
- Preserve adaptive fusion / selected-mask outputs for comparison with the original model.

## Important Paths

- Repo root: `C:/Users/26822/Downloads/data`
- SegFormer source: `C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master`
- CUDA SegFormer env: `D:/users/anaconda3/envs/segformer-phase2/python.exe`
- Current SegFormer export: `experiments/segformer_b1`
- Generated config: `experiments/segformer_b1/configs/segformer_b1_6cls.py`
- Train launcher: `experiments/segformer_b1/train_segformer_b1.ps1`
- Test launcher: `experiments/segformer_b1/test_segformer_b1.ps1`

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

PowerShell equivalent:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1'
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

## Known Pitfalls

- Keep Windows paths in generated mmseg configs as forward-slash paths (`C:/...`). Backslashes such as `C:\Users` can break mmcv/yapf config dumping because of `\U` escape parsing.
- Do not rely on default `python` for SegFormer launchers; explicitly use `D:/users/anaconda3/envs/segformer-phase2/python.exe`.
- TensorBoard is disabled in the generated SegFormer config to avoid extra Windows/Python dependency friction. Text logging remains enabled.
- The generated SegFormer config uses `BN`, not `SyncBN`, for Windows single-GPU training.
- `pretrained/mit_b1.pth` is present under the SegFormer source tree and has been verified with `torch.load`. The generated config can use it with `--pretrained C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/pretrained/mit_b1.pth`.
- The existing web app may already be running at `http://127.0.0.1:8000/`.

## Repository Rules

- Use `rg` / `rg --files` for search.
- Use `apply_patch` for manual file edits.
- Do not revert user changes.
- After each meaningful work chunk, commit and push to `origin/main` unless the user says otherwise.
- Look in `docs/solutions/` for reusable solved-problem notes before re-solving workflow, tooling, environment, or training-entrypoint issues.
