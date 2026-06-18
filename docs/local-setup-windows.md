# Windows Local Setup

这份文档只给本机运行时参考。主 `README.md` 保持通用入口，这里保留 Windows、SegFormer 和本机路径相关的细节。

## Verified Stack

- OS: Windows 10/11
- Python: `D:/users/anaconda3/envs/segformer-phase2/python.exe`
- PyTorch: `1.10.0`
- torchvision: `0.11.1`
- CUDA runtime: `11.3`
- mmcv-full: `1.4.0`
- mmsegmentation: `0.11.0`
- SegFormer source: `C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master`
- MiT-B1 checkpoint: `C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/pretrained/mit_b1.pth`

## Domestic Mirrors

Prefer domestic mirrors when installing packages:

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

## Regenerate SegFormer Assets

Use the local SegFormer source tree and the verified checkpoint:

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' segformer_tools.py --out-dir experiments/segformer_b1 --segformer-repo-root 'C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master' --python-executable 'D:/users/anaconda3/envs/segformer-phase2/python.exe' --pretrained 'C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master/pretrained/mit_b1.pth'
```

The generated config and launcher live under:

- `experiments/segformer_b1/configs/segformer_b1_6cls.py`
- `experiments/segformer_b1/train_segformer_b1.ps1`
- `experiments/segformer_b1/test_segformer_b1.ps1`

## Training

Double-click the launcher from Windows Explorer:

- `run_train_segformer_cuda.bat`
- `resume_train_segformer_cuda.bat`

PowerShell equivalent:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1'
```

Resume from the latest checkpoint:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1' -ResumeLatest
```

Smoke test:

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1' -SmokeTest
```

## Web Demo

Double-click the launcher:

- `run_web_app.bat`

Manual PowerShell launch:

```powershell
& 'C:/Users/26822/Downloads/data/run_web_app.ps1'
```

The Web app should run with the SegFormer source:

```text
--model-source segformer
```

## Windows Notes

- Keep mmseg config paths in forward-slash form to avoid backslash escape issues.
- Use `BN` instead of `SyncBN` for single-GPU Windows training.
- Keep launcher scripts UTF-8 friendly if they contain Chinese characters.
- Do not treat self-IoU or no-GT upload results as ground-truth accuracy.
