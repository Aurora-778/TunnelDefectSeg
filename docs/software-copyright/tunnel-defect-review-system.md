# 隧道病害智能分割与可信复核分析系统 V1.0

## 软件概述

本软件面向隧道巡检图像的病害语义分割、可信复核和可视化展示。系统以图像为输入，输出病害区域掩膜(mask)、叠加图、uncertainty、disagreement、骨架、形态量化指标、风险提示和人工复核优先级。

软件名称建议：

- 隧道病害智能分割与可信复核分析系统 V1.0

软件定位：

- 图像辅助检测与复核软件。
- 输出基于图像的复核信号(image-based review signal)。
- 不提供结构安全诊断、自动养护决策或替代人工验收。

## 主要功能

1. 数据集样本管理：读取隧道图像和人工标注 `GT mask`，支持 6 类标签空间。
2. 模型训练与评估：支持 ResNet50/FCN 历史基线和 SegFormer B1 训练路径。
3. 单图/批量推理：生成 `single mask`、`fused mask`、`selected mask` 和多视图 artifact。
4. 自适应输出选择：比较 single/fused/hybrid 候选，避免 fixed fusion 抑制小病害。
5. 可信复核分析：输出 uncertainty、disagreement、Self IoU、review priority 和复核理由。
6. 形态量化分析：计算面积、连通域、骨架长度、主方向、fragmentation 和 morphology delta。
7. Web 可视化展示：支持浏览器拖拽图片实时检测，并展示原图、mask、overlay、uncertainty、disagreement、skeleton 和结构化报告。
8. 实验证据导出：生成 enhancement evidence summary、patent evidence pack 和 review queue summary。

## 技术特点

- 使用 6 类语义分割标签：`background`、`simple`、`blocky`、`pipeline`、`vertical`、`horizontal`。
- 使用 SegFormer B1 作为当前主干 mask source，最终训练记录达到 `mIoU 84.33`、`mAcc 91.17`、`aAcc 98.62`。
- 使用 probability TTA 生成同源 `fused mask`、entropy uncertainty 和 disagreement。
- 使用 adaptive selection 从候选 mask 中选择最终输出，并记录选择理由。
- 使用 morphology delta 解释 single/fused/selected 之间的面积、连通域、骨架长度和方向变化。
- 使用 review queue 将批量样本按复核优先级排序，GT 仅用于事后评估队列效果。

## 运行环境

推荐环境：

- 操作系统：Windows 10/11
- Python：3.8 到 3.11，SegFormer 训练推荐 `segformer-phase2` 环境
- GPU：支持 CUDA 的 NVIDIA GPU，当前验证设备为 RTX 3060 Laptop GPU
- 深度学习框架：PyTorch、torchvision、mmcv-full、mmsegmentation
- Web 运行方式：本地 HTTP 服务，默认地址 `http://127.0.0.1:8000`

主要依赖：

- `torch`
- `torchvision`
- `numpy`
- `opencv-python`
- `Pillow`
- `mmcv-full`
- `mmsegmentation`
- `timm`
- `pytest`

第三方依赖仅作为运行框架或模型组件使用，不应在软件著作权材料中描述为本软件自研源码。

## 模块结构

| 模块 | 主要文件 | 说明 |
|---|---|---|
| 数据适配 | `data_adapter.py` | 发现样本、读取图像与 mask、固定数据划分 |
| 指标计算 | `metrics_adapter.py` | 混淆矩阵、PA、mIoU、mDice |
| SegFormer 配置生成 | `segformer_tools.py` | 生成 mmseg 数据目录、配置和训练脚本 |
| 可信推理 | `run_confidence_risk.py` | 输出 mask、overlay、uncertainty、disagreement、skeleton 和 report |
| 自适应融合 | `adaptive_fusion.py` | single/fused/hybrid 候选选择 |
| 形态分析 | `morphology_adapter.py` | 面积、连通域、骨架、方向、morphology delta |
| 风险与复核 | `risk_adapter.py` | risk、review priority 和理由 |
| 证据汇总 | `evaluate_confidence_risk.py`、`enhancement_evidence.py` | full evaluation、evidence summary、patent pack、review queue |
| Web 展示 | `web_app.py`、`web_demo/index.html` | 拖拽上传、实时检测、多视图展示 |

## 操作流程

1. 启动 Web 应用：双击 `run_web_app.bat`，或运行 `web_app.py`。
2. 打开浏览器：访问 `http://127.0.0.1:8000`。
3. 选择数据集样本或拖入自选图片。
4. 系统生成多视图结果：原图、single mask、fused mask、selected mask、overlay、uncertainty、disagreement、skeleton。
5. 查看结构化报告：类别、面积、连通域、骨架长度、方向、risk、review priority 和复核理由。
6. 若使用有 GT 的数据集样本，可查看真实 mIoU 和 error overlap；若为自选上传图片，则真实 mIoU 显示为 `N/A`。

## 输出文件

单图推理输出：

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

批量证据输出：

- `experiments/enhancement_evidence_test_summary.json`
- `experiments/enhancement_evidence_all_summary.json`
- `experiments/patent_evidence_test_pack.json`

## 界面截图建议

软著说明书截图可选择：

1. Web 首页和拖拽上传区域。
2. 多视图检测结果区域。
3. 原图、selected overlay、uncertainty、disagreement、skeleton 组合视图。
4. 形态量化明细表。
5. Review queue / 增强模块优势证据面板。
6. JSON structured report 面板。

## 权属边界说明

本软件自研部分主要包括数据适配、训练配置生成、可信推理流程、自适应 mask 选择、形态量化、风险/复核优先级、证据汇总和 Web 展示代码。

第三方框架、公开模型结构、Python 依赖、CUDA/PyTorch/mmsegmentation 环境和数据集本身不作为本软件自研源码主张。训练得到的 checkpoint 和实验结果属于软件运行产物，应与源代码材料分开整理。
