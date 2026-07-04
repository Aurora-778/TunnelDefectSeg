# 视频输入扩展说明

## 当前定位

当前视频输入能力用于验证异步逐帧分析流程，不是实时视频流分析，也不是生产级视频巡检系统。

`tunnel_demo.mp4` 是由 KICT 静态裂缝图像和 mask 按顺序合成的 demo video。它的作用是验证：

1. KICT image / mask 到 demo video 的可复现生成；
2. 视频抽帧；
3. 视频帧 metadata 估算；
4. 已有 mask 的逐帧几何特征提取；
5. video 版 `inspection_sequence.csv` 生成。

该视频不应被解释为真实机器人连续巡检视频。

## 数据边界

当前 demo video 的视觉内容来自 KICT 静态图像，mask 来自 KICT 静态标注。视频帧时间、里程、环号和位姿来自脚本按视频时间估算的 metadata。

当输出中出现：

```text
metadata_source = estimated_from_video_time
metadata_limit_note = mileage and ring_id are estimated, not real robot localization
```

表示里程和环号不是机器人真实定位结果。

当输出中出现：

```text
feature_source = mask_nonzero_pixels
feature_limit_note = features are extracted from provided masks, not model inference; risk_level is rule_based_area_only
```

表示病害几何特征来自已提供 mask 的非零像素，不是模型推理结果。

## 运行流程

生成 KICT demo video：

```bash
python scripts/create_demo_tunnel_video_from_kict.py \
  --image_root data/kict/images \
  --mask_root data/kict/masks \
  --output_video data/videos/tunnel_demo.mp4 \
  --output_masks_dir data/video_masks/tunnel_demo \
  --output_manifest data/video_demo/tunnel_demo_source_manifest.csv \
  --num_frames 100 \
  --fps 10 \
  --width 640 \
  --height 480
```

运行 video inspection pipeline：

```bash
python scripts/run_video_inspection_pipeline.py \
  --video_path data/videos/tunnel_demo.mp4 \
  --video_id tunnel_demo \
  --sample_interval 10 \
  --max_frames 100 \
  --mode mask_input \
  --masks_dir data/video_masks/tunnel_demo
```

## 输出目录

视频相关输出使用独立目录：

```text
data/videos/
data/video_demo/
data/video_frames/
data/video_masks/
data/video_inspection/
```

这些输出不会覆盖 `data/simulated/` 下的原有 KICT simulated pipeline 产物。

## 后续扩展

如果输入真实原始视频，需要额外增加模型推理模块，将每帧图像转换为 mask 或检测结果。当前 `model_inference` 模式保留为后续工作，不在本阶段实现。

`supervision` 可以作为后续 Round 5B 的可视化和检测结果统一层，但本阶段不引入该依赖。
