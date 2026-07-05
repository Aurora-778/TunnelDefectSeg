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
2. 基于 `supervision_detections_manifest.csv`、已有视频帧和 mask 生成 `outputs/video_inspection/<video_id>/supervision_annotated_frames/`；
3. 同步生成 `outputs/video_inspection/<video_id>/supervision_visualization_manifest.csv`，便于后续 Web 或报告读取；
4. 将 supervision 标注帧导出为 `outputs/video_inspection/<video_id>/supervision_annotated_video.mp4`。

其中 `confidence` 字段在当前 mask-input demo 中固定为 `1.0`，不代表模型置信度。该值只表示当前记录来自已提供 mask 的结构化转换。

## 可选依赖

主项目环境不强制安装 `supervision`。如需运行本层功能，可单独安装：

```bash
python -m pip install -r requirements-video.txt
```

`requirements-video.txt` 独立于主 `requirements.txt`，避免影响原有稳定 pipeline。

当前建议安装版本为 `supervision>=0.20`。如果后续 supervision API 发生变化，应优先使用本项目测试通过的版本范围，或在可选视觉脚本中补充兼容处理。

## 运行前置条件

运行 Step 3 前，需要先运行 Step 1 和 Step 2，生成 `tunnel_demo` 的 frames、masks 和 `disease_features.csv`。至少应存在：

```text
data/video_frames/tunnel_demo/
data/video_masks/tunnel_demo/
data/video_inspection/tunnel_demo/disease_features.csv
```

如果仓库中未包含原始 KICT `images/` 和 `masks/`，请先准备 KICT 数据，并将 `--image_root` / `--mask_root` 指向实际路径。

最小运行顺序如下：

```bash
python scripts/create_demo_tunnel_video_from_kict.py --image_root /path/to/KICT/images --mask_root /path/to/KICT/masks --video_id tunnel_demo
python scripts/run_video_inspection_pipeline.py --video_id tunnel_demo
python scripts/annotate_video_frames.py --video_id tunnel_demo
python scripts/export_annotated_video.py --video_id tunnel_demo
python scripts/convert_video_features_to_detections.py --video_id tunnel_demo --overwrite
python scripts/annotate_video_frames_supervision.py --video_id tunnel_demo --overwrite
python scripts/export_supervision_annotated_video.py --video_id tunnel_demo --overwrite
```

`--overwrite` 只用于明确允许覆盖旧的 supervision 可视化输出；默认情况下脚本会拒绝覆盖已有产物，避免旧帧混入新视频。

## 边界说明

当前 supervision 输出仍然基于 KICT demo video、已有 mask、`disease_features.csv` 和转换后的 `supervision_detections_manifest.csv`。它不是实时视频流系统，也不是模型推理系统。

本项目核心仍然是病害记忆库、跨巡检关联、变化分析、重点复检清单和工程报告。supervision 只增强视觉展示和检测结果结构化表达。

`supervision_annotated_video.mp4` 是可视化展示产物，不代表真实工业在线分析结果。未安装 supervision 时，主 pipeline、Step 1 和 Step 2 不受影响。
