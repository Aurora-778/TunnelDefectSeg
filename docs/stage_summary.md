# 项目阶段总结

## 阶段 1：机器人巡检仿真表生成

目标：构造机器人连续巡检的时间、里程、环号、方位和病害对象基础数据。

输入：脚本内置仿真参数。

输出：

```text
data/simulated/inspection_sequence.csv
data/simulated/disease_instances.csv
data/simulated/frame_disease_mapping.csv
data/simulated/disease_growth_records.csv
```

实现结果：已经形成多次巡检、多帧图像和多个病害对象的仿真表。

当前限制：巡检路径、里程、环号和病害增长关系均为仿真，不是真实机器人采集结果。

## 阶段 2：KICT 数据检查与 mask 特征提取

目标：检查 KICT 图像和 mask 匹配关系，并提取裂缝几何特征。

输入：

```text
KICT/images/
KICT/masks/
```

输出：

```text
outputs/kict_preview/
data/simulated/kict_mask_features.csv
```

实现结果：完成 image-mask 匹配检查、预览图生成和 mask 面积、bbox、中心点等字段提取。

当前限制：KICT 是静态公开图像数据集，不包含真实连续巡检时间序列。

## 阶段 3：KICT 特征与仿真表融合

目标：将 KICT 真实图像路径、mask 路径和几何特征接入机器人巡检仿真帧。

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

实现结果：生成同时包含工程元数据和真实 mask 几何特征的核心记录表。

当前限制：同一 KICT 样本可能循环分配给多个仿真帧，主要用于流程验证。

## 阶段 4：工程化病害描述生成

目标：按巡检和病害对象生成工程化中文描述。

输入：

```text
data/simulated/robot_kict_frame_records.csv
```

输出：

```text
data/simulated/disease_engineering_report.csv
outputs/disease_engineering_report.md
```

实现结果：输出病害编号、类型、里程、环号、方位、面积、风险等级和中文描述。

当前限制：工程位置基于仿真元数据，不能作为真实现场定位结论。

## 阶段 5：跨巡检增长变化分析

目标：比较同一病害对象在不同巡检中的面积和风险变化。

输入：

```text
data/simulated/disease_engineering_report.csv
```

输出：

```text
data/simulated/disease_growth_analysis.csv
outputs/disease_growth_analysis_report.md
```

实现结果：输出首次面积、末次面积、增长率、风险变化、趋势和关注等级。

当前限制：增长趋势主要用于验证时空聚合和表达流程，不代表真实病害长期演化规律。

## 阶段 6：可视化与重点复检清单

目标：把增长分析结果转化为图表和复检优先级。

输入：

```text
data/simulated/disease_growth_analysis.csv
```

输出：

```text
data/simulated/priority_recheck_list.csv
outputs/recheck_list_report.md
outputs/visualizations/*.png
```

实现结果：生成关注等级、增长趋势、风险变化、面积增长率、病害类型和里程风险图表。

当前限制：复检建议是规则化原型输出，仍需要人工确认。

## 阶段 7：交付文档整理

目标：让老师或其他使用者能理解项目主线、复现流程和数据边界。

输入：已有脚本、CSV、Markdown 报告和图表。

输出：

```text
README.md
requirements.txt
docs/run_pipeline.md
docs/stage_summary.md
outputs/project_review_report.md
```

实现结果：补齐中文 README、运行流程、阶段总结和项目检查报告。

当前限制：后续若接入真实机器人数据，需要同步更新文档中的数据来源和边界说明。
