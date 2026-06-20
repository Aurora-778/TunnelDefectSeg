# Spatial Mapping Method

这份说明对应当前已经落地的空间定位原型。它的目标是把病害几何结果翻译成工程上可读的位置表达，而不是伪造外业级精度。

## What It Takes In

输入分两层：

- 几何：`mask`、`bbox`、`point`、`polyline`
- 元数据：`image_shape`、`ring_id`、`mileage`、`camera_intrinsics`、`depth`、`camera_pose`

当前代码里，最稳定的输入是 selected mask 和 bbox 类几何。没有几何中心时，定位结果会返回 unavailable，而不是硬算。

## What It Outputs

输出字段和合同如下：

- `status`: `available` / `partial` / `unavailable`
- `source`: `simulation` / `calibration` / `sensor` / `not_provided`
- `location_source`: 和 `source` 同步的对外展示字段
- `accuracy_level`: `coarse` / `calibrated` / `metric`
- `method`: 当前默认是 `mask_centroid_clock_mapping`
- `pixel_center`: 像素中心点
- `normalized_center`: 归一化图像位置
- `clock_position`: 隧道时钟方位
- `ring_id` / `mileage`: 可选工程语义
- `local_3d`: 可选局部三维坐标
- `limitations`: 结果边界说明

## How It Works

### 1. Geometry to center point

- mask 先取前景像素外接框，再得到中心点
- bbox 直接用几何中心点
- point 直接作为中心点
- polyline 先取包围框，再求中心点

### 2. Center point to clock position

把病害中心点相对图像中心的位置换成时钟方位：

- 上方更接近 12 点
- 右侧更接近 3 点
- 下方更接近 6 点
- 左侧更接近 9 点

当前实现是图像平面近似，所以它适合做 prototype 和展示，不适合宣称外业级定位。

### 3. Metadata gating

- 有 `ring_id`、`mileage` 就保留工程语义
- `location_source` 用来告诉展示层这条位置来自 simulation、calibration 还是 sensor
- 有 `camera_intrinsics` 和 `depth` 才输出 `local_3d`
- 缺少元数据时，保留图像位置和时钟方位，但把边界写进 `limitations`

## Boundary Rules

- 不能把 `simulation` 或 `calibration` 输出写成实测
- 不能把 `available` 理解成 field-grade 精度
- 不能把 `local_3d.status = available` 理解成已经完成外业验证

## Evidence In This Repo

- `spatial_mapping.py`
- `run_confidence_risk.py`
- `multidomain_schema.py`
- `multidomain_detectors.py`
- `web_demo/index.html`
- `tests/test_spatial_mapping.py`
- `tests/test_run_confidence_risk_spatial_mapping.py`

## How To Present It

答辩时建议这么说：

“我们现在不只是输出病害 mask，还能把 mask 翻译成工程位置。当前版本能给出图像中心、钟位、环号/里程和可选三维坐标，但只有在有标定和深度条件时才给更高精度；没有这些条件时，系统会明确标注为 simulation/calibration prototype。”
