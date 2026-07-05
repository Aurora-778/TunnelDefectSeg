# Supervision 可选视觉层说明

## 当前定位

`supervision` 在本项目中只是一个可选视觉工具层，用于把已有的视频帧、mask 和 `disease_features.csv` 转换成更标准的检测 / 分割结果结构，并生成增强版标注帧和标注视频。

它不改变项目核心算法，也不参与机器人巡检病害分析主流程。

## 不负责的内容

`supervision` 不负责：

- Disease Memory Bank；
- no-id Association；
- Growth Analysis；
- 重点复检清单生成；
- 工程化报告生成；
- 实时视频流分析；
- Web 上传；
- 模型自动推理。

这些能力仍由项目原有的表格、规则和 Orchestrator pipeline 负责。

## 负责的内容

本阶段新增的 supervision 视觉层主要负责：

1. 将 `data/video_inspection/<video_id>/disease_features.csv` 转换为 `outputs/video_inspection/<video_id>/supervision_detections_manifest.csv`；
2. 基于已有视频帧和 mask 生成 `outputs/video_inspection/<video_id>/supervision_annotated_frames/`；
3. 将 supervision 标注帧导出为 `outputs/video_inspection/<video_id>/supervision_annotated_video.mp4`。

其中 `confidence` 字段在当前 mask-input demo 中固定为 `1.0`，不代表模型置信度。该值只表示当前记录来自已提供 mask 的结构化转换。

## 可选依赖

主项目环境不强制安装 `supervision`。如需运行本层功能，可单独安装：

```bash
python -m pip install -r requirements-video.txt
```

`requirements-video.txt` 独立于主 `requirements.txt`，避免影响原有稳定 pipeline。

## 边界说明

当前 supervision 输出仍然基于 KICT demo video、已有 mask 和 `disease_features.csv`。它不是实时视频流系统，也不是模型推理系统。

本项目核心仍然是病害记忆库、跨巡检关联、变化分析、重点复检清单和工程报告。supervision 只增强视觉展示和检测结果结构化表达。
