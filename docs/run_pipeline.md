# 运行流程说明

本流程用于复现机器人隧道巡检病害监测原型。流程不会训练模型，不会下载数据，也不会修改原始 KICT 数据集。

## 1. 准备 KICT 数据集

KICT 数据集根目录应包含：

```text
images/
masks/
```

图像支持 `.jpg`、`.jpeg`、`.png`、`.bmp`，mask 支持 `.png`、`.jpg`、`.jpeg`、`.bmp`。脚本以文件名 stem 进行匹配，例如 `images/0001.jpg` 对应 `masks/0001.png`。

## 2. 生成仿真巡检表

```bash
python scripts/generate_simulation_tables.py
```

作用：生成机器人巡检过程中的时间、里程、环号、方位、病害对象和跨巡检增长记录。

输入：

```text
脚本内置仿真参数
```

输出：

```text
data/simulated/inspection_sequence.csv
data/simulated/disease_instances.csv
data/simulated/frame_disease_mapping.csv
data/simulated/disease_growth_records.csv
```

## 3. 检查 KICT 数据集

```bash
python scripts/inspect_kict_dataset.py --dataset-root /path/to/KICT
```

作用：检查 `images/` 与 `masks/` 数量和匹配关系，并随机生成 5 组预览图。

输入：

```text
KICT/images/
KICT/masks/
```

输出：

```text
outputs/kict_preview/
```

## 4. 提取 KICT mask 几何特征

```bash
python scripts/extract_kict_mask_features.py --dataset-root /path/to/KICT
```

作用：把 mask 中非零像素视为裂缝区域，提取面积、bbox、中心点、mask 宽高等几何特征。

输入：

```text
KICT/images/
KICT/masks/
```

输出：

```text
data/simulated/kict_mask_features.csv
```

## 5. 合并 KICT 与仿真巡检表

```bash
python scripts/merge_kict_with_simulation.py
```

作用：将 KICT 真实图像路径、mask 路径和几何特征接入有病害的仿真巡检帧。

输入：

```text
data/simulated/inspection_sequence.csv
data/simulated/frame_disease_mapping.csv
data/simulated/kict_mask_features.csv
```

输出：

```text
data/simulated/robot_kict_frame_records.csv
outputs/robot_kict_merge_report.md
```

## 6. 生成工程化病害报告

```bash
python scripts/generate_engineering_report.py
```

作用：按 `inspection_id` 和 `disease_id` 聚合图像帧，生成病害位置、面积、风险等级和中文工程描述。

输入：

```text
data/simulated/robot_kict_frame_records.csv
```

输出：

```text
data/simulated/disease_engineering_report.csv
outputs/disease_engineering_report.md
outputs/disease_engineering_report_summary.md
```

## 7. 跨巡检增长分析

```bash
python scripts/analyze_disease_growth.py
```

作用：比较同一 `disease_id` 在首次和末次巡检中的面积、风险等级和趋势变化。

输入：

```text
data/simulated/disease_engineering_report.csv
```

输出：

```text
data/simulated/disease_growth_analysis.csv
outputs/disease_growth_analysis_report.md
outputs/disease_growth_analysis_summary.md
```

## 8. 生成可视化和复检清单

```bash
python scripts/generate_visualization_and_recheck_list.py
```

作用：生成增长分析图表、重点复检清单和可视化总结报告。

输入：

```text
data/simulated/disease_growth_analysis.csv
```

输出：

```text
data/simulated/priority_recheck_list.csv
outputs/recheck_list_report.md
outputs/visualization_report.md
outputs/visualization_summary.md
outputs/visualizations/attention_level_distribution.png
outputs/visualizations/growth_trend_distribution.png
outputs/visualizations/risk_level_change_distribution.png
outputs/visualizations/top10_area_growth_rate.png
outputs/visualizations/disease_type_distribution.png
outputs/visualizations/mileage_risk_distribution.png
```

## 9. 启动 Web Dashboard

```bash
python web_app.py --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/
```

## 数据边界

本流程使用 KICT Tunnel Crack Segmentation Dataset 中的真实隧道裂缝图像和 mask 几何特征；机器人巡检过程中的时间、里程、环号、方位、`disease_id` 以及跨巡检增长关系为仿真元数据。本系统当前用于验证机器人巡检场景下病害工程化描述、时空聚合、增长分析和复检清单生成流程，不代表真实隧道病害长期演化规律。
