# 隧道病害分割与 Web 展示项目

这个仓库用于做隧道病害语义分割实验、增强模块验证和本地 Web 可视化展示。当前项目已经从早期 `ResNet50_FCN` 基线升级到 `SegFormer B1` 训练路径，并保留了单次预测、TTA 融合、adaptive selection、uncertainty、disagreement、skeleton 等结果，方便对比和向老师展示。

## 当前任务目标

- 训练 6 类隧道病害分割模型。
- 使用 SegFormer B1 提升 mask 质量。
- 在 Web 端支持拖拽图片实时检测。
- 展示原图、GT 标注、单次 mask、融合 mask、选择 mask、叠加图、不确定性图、分歧图、骨架图和报告指标。
- 保留增强模块的实验数据，用于说明创新点和阶段性效果。

## 类别定义

固定使用 6 类标签空间：

| ID | Class | 中文说明 |
|---:|---|---|
| 0 | background | 背景 |
| 1 | simple | simple 类病害 |
| 2 | blocky | 块状病害 |
| 3 | pipeline | 管线类病害 |
| 4 | vertical | 竖向病害 |
| 5 | horizontal | 横向病害 |

## 数据与训练约定

- 输入尺寸：`384 x 384`
- 数据划分：`train / val / test = 700 / 150 / 150`
- 标签模式：`multiclass`
- 训练轮次：ResNet50 基线默认 `200 epoch`
- mmseg / SegFormer 当前训练：`160000 iter`
- mask 会在读取时统一转换到 6 类标签空间。
- 最终评价主要看 `mIoU`、`mAcc`、`aAcc`，以及每个类别的 `IoU / Acc`。

## 关键文件

- `train_resnet50.py`：早期 ResNet50/FCN 训练与评估入口，支持 `AUTO_RESUME=1` 断点续训。
- `data_adapter.py`：发现样本、固定划分数据集、读取图像和 mask。
- `metrics_adapter.py`：流式混淆矩阵指标，计算 `PA`、`mIoU`、`mDice`。
- `build_6class_labels.py`：重新生成 `multiclass_labels/`、`class_manifest.csv` 和 `class_summary.json`。
- `segformer_tools.py`：生成 SegFormer/mmseg 数据配置和启动脚本。
- `run_confidence_risk.py`：推理后增强模块，输出 mask、uncertainty、disagreement、skeleton 和 JSON 报告。
- `web_app.py`：本地 Web 检测服务，支持拖拽上传图片并实时生成多视图结果。
- `web_demo/index.html`：Web 前端展示界面。

## 数据目录

原始数据按类别目录组织：

```text
1/images    1/labels
2/images    2/labels
3/images    3/labels
4/images    4/labels
5/images    5/labels
```

`build_6class_labels.py` 运行后会生成：

- `multiclass_labels/`：统一后的 6 类 mask。
- `class_manifest.csv`：图像与标签配对清单。
- `class_summary.json`：每类图像数和像素统计。

当前快照：

- 总样本数：`1000`
- 每个病害类别：`200` 张图像

## SegFormer B1 训练

SegFormer 路径用于替换早期 ResNet50/FCN 基线。它更适合细长裂缝、折角和复杂边缘的分割。

重新生成 SegFormer 配置：

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' segformer_tools.py --out-dir experiments/segformer_b1 --segformer-repo-root 'C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master' --python-executable 'D:/users/anaconda3/envs/segformer-phase2/python.exe'
```

双击训练：

```text
run_train_segformer_cuda.bat
```

双击从最新 checkpoint 继续训练：

```text
resume_train_segformer_cuda.bat
```

PowerShell 等价命令：

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1'
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1' -ResumeLatest
```

训练入口冒烟测试：

```powershell
& 'C:/Users/26822/Downloads/data/run_train_segformer_cuda.ps1' -SmokeTest
```

已有阶段结果：

| Iter | mIoU | mAcc | aAcc | 说明 |
|---:|---:|---:|---:|---|
| 160000 | 84.33% | 91.17% | 98.62% | SegFormer B1 已完成主干训练 |

每类结果：

| Class | IoU | Acc |
|---|---:|---:|
| background | 99.01 | 99.51 |
| simple | 80.23 | 88.40 |
| blocky | 53.58 | 75.56 |
| pipeline | 94.16 | 97.43 |
| vertical | 95.85 | 97.75 |
| horizontal | 83.17 | 88.34 |

当前短板主要是 `blocky` 类，后续优化可继续围绕难例、边界、小目标和类别混淆展开。

## ResNet50 基线

保留 ResNet50/FCN 是为了做历史对比和增强模块兼容验证。

断点续训基线：

```text
run_train_resnet50.bat
run_train_resnet50_visible.ps1
```

干净重训：

```text
run_train_resnet50_fixed.bat
run_train_resnet50_fixed_visible.ps1
```

中断后继续：

```text
run_train_resnet50_fixed_resume.bat
run_train_resnet50_fixed_resume_visible.ps1
```

早期输出目录：

```text
experiments/ResNet50_FCN_6cls
experiments/ResNet50_FCN_6cls_fixed
```

旧实验目录中的历史报告不能当作当前 SegFormer 的最终结果使用。

## 增强模块

`run_confidence_risk.py` 是推理后的 confidence-risk 模块。它不重新训练主干模型，而是在模型输出之后做以下处理：

1. 对同一张图做轻量 TTA，例如原图和水平翻转。
2. 将不同增强视角的概率图对齐。
3. 生成 `single mask`、`fused mask` 和 `hybrid mask`。
4. 根据小目标保护、置信度、连通区域和形态信息选择 `selected_mask`。
5. 计算 `uncertainty`、`disagreement`、骨架、方向、面积和风险解释。

单张图或文件夹推理：

```powershell
python run_confidence_risk.py <image-or-folder> --output-dir experiments/confidence_risk --tta-mode light
```

使用 SegFormer checkpoint 推理：

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' run_confidence_risk.py <image-or-folder> --model-source segformer --segformer-config experiments/segformer_b1/configs/segformer_b1_6cls.py --segformer-checkpoint experiments/segformer_b1/runs/segformer_b1_6cls/latest.pth --output-dir experiments/segformer_b1/confidence_risk
```

`report.json` 中会包含 `mask_source`，用于区分：

- `legacy_resnet50_fcn`
- `segformer_b1`

SegFormer 模式下，identity 和 horizontal-flip 的 probability maps 会对齐并平均，用来生成 `fused_mask`、entropy uncertainty 和 TTA disagreement。adaptive selection 逻辑保持模型无关。

每张图的主要输出产物：

- `<stem>_single_mask.png`
- `<stem>_fused_mask.png`
- `<stem>_hybrid_mask.png`
- `<stem>_selected_mask.png`
- `<stem>_overlay.png`
- `<stem>_selected_overlay.png`
- `<stem>_uncertainty_heatmap.png`
- `<stem>_disagreement_heatmap.png`
- `<stem>_skeleton.png`
- `<stem>_report.json`

`selected_mask` 是从 single、fused、hybrid 候选中自适应选择的结果。它用于默认叠加图、形态分析、骨架提取和风险报告。

报告里的 `Self IoU` 只比较模型自己的 single 和 fused 输出是否一致；它不是 ground-truth mIoU（not ground-truth mIoU），不能代替有 GT mask 时的真实精度。

## 增强模块阶段证据

在有 GT 标注的 split 上，可以评估 single、fixed fused 和 selected：

```powershell
python evaluate_confidence_risk.py --split test --limit 20 --output experiments/confidence_risk_eval.json
```

adaptive threshold 只应在验证集搜索，避免把 test/all 用成调参集：

```powershell
python evaluate_confidence_risk.py --split val --limit 0 --search-config --output experiments/adaptive_fusion_config_search_val.json
```

当前 full-run 证据：

| Split | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fused |
|---|---:|---:|---:|---:|---:|
| `val` | 150 | 0.3238 | 0.3091 | 0.3261 | +0.0170 |
| `test` | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 |
| `all` | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 |

这些结果说明：固定融合会损失一部分细小病害，adaptive selected 可以明显恢复 fixed fused 的损失，并保留 uncertainty、disagreement、overlay、skeleton、morphology、risk evidence。它不是保证每个 split 都超过 single 的新 backbone，而是一个更稳妥、更可解释的后处理增强模块。

更多实验表格见：

```text
docs/experiments/enhancement-evidence-summary.md
```

不确定性到错误区域的证据：

| Split | Protected pixels vs fused | Successful guard events | Error coverage | HU error precision |
|---|---:|---:|---:|---:|
| `test` | 156,301 | 72 / 150 | 45.66% | 80.66% |
| `all` | 1,230,616 | 614 / 1000 | 44.54% | 78.27% |

`Error coverage` 和 `HU error precision` 需要 GT mask。当前像素级 high-uncertainty 阈值是 `0.35`，样本级 review fraction 阈值是 `0.5`。

## U6 可信复核优先级

当前增强模块已继续扩展到 U6：在原有 `risk` 之外新增 `review_priority`。两者含义不同：

- `risk`：图像病害风险提示，主要来自类别、面积、连通域、骨架和 uncertainty。
- `review_priority`：人工复核排序信号，综合 risk、uncertainty、disagreement、single/fused 自一致性、fixed fusion 收缩和 selected-mask 理由。

有 GT 的数据集样本还会输出 `uncertainty_calibration`，按 uncertainty 分桶比较平均不确定性与真实错误率，形成 ECE-like 校准证据。自选上传图片没有 GT，因此不会显示真实 mIoU 或 error overlap，只显示无 GT 可计算的 `review_priority`、Self IoU、uncertainty、disagreement、morphology 和 selected-mask evidence。

`review_priority` 只表示“建议人工优先复核这张图”，不是结构安全诊断，也不是最终养护决策。

专利/创新点说明见：

```text
docs/patent-notes/tunnel-defect-confidence-risk.md
```

## U7 专利/软著证据闭环

当前项目已进一步补充 U7 证据闭环，用于让老师检查、专利交底和软著材料更容易复用：

- `patent evidence pack`：把 backbone evidence、enhancement evidence、代表案例、artifact 路径模板、artifact 可用性标记和 claim boundaries 放在同一份 JSON。
- `morphology_delta`：解释 single/fused/selected mask 之间的面积、连通域、骨架长度和方向变化。
- `review_queue_summary`：按模型内部证据生成批量复核队列，GT 只用于事后评估队列是否覆盖真实错误或 fixed fusion 损伤。
- `software-copyright`：补充软著说明书草稿和源代码材料清单。

当前 test split evidence pack：

```text
experiments/patent_evidence_test_pack.json
```

## Web 实时展示

`web_app.py` 提供本地拖拽检测服务。上传图片后，服务会运行 confidence-risk pipeline，并返回：

- 原图
- GT 标注（仅数据集样本有）
- 单次 mask
- 融合 mask
- 选择 mask
- GT 叠加图
- 预测叠加图
- 选择叠加图
- 不确定性图
- 分歧图
- 骨架图
- JSON 风险报告
- Review priority 复核优先级
- Review queue / 典型案例证据

双击启动：

```text
run_web_app.bat
```

或使用 SegFormer 环境手动启动：

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' web_app.py --host 127.0.0.1 --port 8000 --model-source segformer
```

打开：

```text
http://127.0.0.1:8000
```

检查后端是否用了 SegFormer：

```text
http://127.0.0.1:8000/api/health
```

期望看到：

```json
{
  "model_source": "segformer"
}
```

自选上传图片通常没有 GT mask，所以真实 `mIoU` 应显示为 `N/A`。这时界面仍可以显示 `Self IoU`、uncertainty、disagreement、形态量化、骨架和 selected-mask 选择理由。没有 GT 时，系统不能显示 true mIoU，也不应把模型自己的输出当成 GT。

Web 生成结果默认写入：

```text
experiments/web_live/
```

该目录是运行时产物，已被 git 忽略。

## PPT 与展示材料

项目展示材料已经围绕“做什么、为什么做、创新在哪、结果如何展示”组织。PPT 里使用的 Web demo 图应与 `web_demo` 当前展示资产保持一致，避免 PPT 和网页骨架图来源不一致。

常用材料目录：

```text
docs/
experiments/
web_demo/
```

## 软著材料草稿

项目已补充软著说明材料草稿，便于后续整理申请文档：

```text
docs/software-copyright/tunnel-defect-review-system.md
docs/software-copyright/source-material-checklist.md
```

建议软件名称为：

```text
隧道病害智能分割与可信复核分析系统 V1.0
```

软著材料应重点描述本仓库自研的软件表达：数据适配、训练配置生成、可信推理流程、自适应 mask 选择、形态量化、风险/复核优先级、证据汇总和 Web 展示。第三方框架、公开模型结构、模型权重、数据集图片和本地索引文件不应作为自研源码主张。

## 环境注意事项

SegFormer 训练和 Web 推理建议使用：

```text
D:/users/anaconda3/envs/segformer-phase2/python.exe
```

已验证的核心版本：

- `torch==1.10.0`
- `torchvision==0.11.1`
- CUDA runtime `11.3`
- `mmcv-full==1.4.0`
- `mmsegmentation==0.11.0`

本机 SegFormer 源码路径：

```text
C:/Users/26822/Desktop/隧道病害检测/third_party/SegFormer-master
```

国内镜像安装示例：

```powershell
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <packages>
pip install -i http://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com <packages>
```

CUDA PyTorch 安装示例：

```powershell
& 'D:/users/anaconda3/Scripts/conda.exe' install -n segformer-phase2 -y pytorch==1.10.0 torchvision==0.11.1 cudatoolkit=11.3 -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main --override-channels
```

mmcv wheel 安装示例：

```powershell
& 'D:/users/anaconda3/envs/segformer-phase2/python.exe' -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --force-reinstall mmcv-full==1.4.0 -f https://download.openmmlab.com/mmcv/dist/cu113/torch1.10.0/index.html
```

## 常见坑

- Windows 路径在 mmseg config 中尽量使用正斜杠，例如 `C:/Users/...`，避免 `\U` 转义问题。
- SegFormer 脚本不要依赖默认 `python`，应显式使用 `D:/users/anaconda3/envs/segformer-phase2/python.exe`。
- Windows 单卡训练使用 `BN`，不要用 `SyncBN`。
- 外部 SegFormer 源码已做过兼容补丁：`np.float` 改为 `np.float64`。
- mmcv text logger 已做过兼容补丁，避免验证日志缺少 `data_time` 时崩溃。
- `pretrained/mit_b1.pth` 位于 SegFormer 源码目录下，可作为 B1 预训练权重。
- Web live detection 默认应使用 `--model-source segformer`。
- `.codegraph/` 是本地代码索引，不应提交到仓库。

## 推荐检查命令

运行文档与展示契约测试：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_docs_artifact_contract.py
```

运行 Web 相关测试：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_web_app.py
```

检查 CUDA 环境：

```powershell
@'
import torch, torchvision, cv2, mmcv, timm
from mmcv.ops import CrissCrossAttention
print(torch.__version__, torch.version.cuda, torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda')
print(torchvision.__version__, cv2.__version__, mmcv.__version__, timm.__version__)
'@ | & 'D:/users/anaconda3/envs/segformer-phase2/python.exe' -
```
