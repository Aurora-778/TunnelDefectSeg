# 机器人隧道巡检病害识别、时空聚合与增长监测系统

本项目面向机器人隧道巡检场景：机器人在隧道内连续行进并拍摄图像，系统识别病害区域，并结合时间、里程、环号、方位等工程信息，形成可追踪的病害对象、增长分析和重点复检清单。

当前主线不是单张图片分割 Demo，而是把真实图像 mask 几何信息接入仿真巡检流程，验证“图像病害识别 -> 工程化定位描述 -> 病害对象记忆 -> 跨巡检关联 -> 增长分析 -> 风险评分 -> Web 展示 -> 最终报告”的完整应用闭环。

## 推荐运行方式

```bash
python run.py --mode full_pipeline
```

该命令是根目录薄封装，实际执行会进入 Orchestrator DAG。也可以直接运行：

```bash
python orchestrator/run.py --dag config/dag.yaml
```

两种方式都会基于仓库中已经生成的 `data/simulated/robot_kict_frame_records.csv`，一键生成工程化报告、增长结果、Disease Memory Bank、带评分的病害关联记录、复检清单、可视化图表和最终项目报告。

## 当前实现功能

1. KICT 隧道裂缝 image/mask 数据检查。
2. mask 面积、bbox、中心点等几何特征提取。
3. 机器人巡检仿真元数据生成。
4. 图像帧与 `disease_id` 关联。
5. KICT mask 几何特征与仿真巡检帧合并。
6. 工程化中文病害描述生成。
7. 跨巡检增长变化分析。
8. 风险等级与关注等级判断。
9. 重点复检清单生成。
10. 图表可视化输出。
11. Web Dashboard 展示巡检总览、工程报告、增长分析、复检清单和图表。
12. Disease Memory Bank 病害对象记忆库生成。
13. 病害对象与机器人巡检帧的关联记录生成。
14. 一键完整 pipeline 入口与最终项目报告生成。
15. AI Multi-Agent Engineering Platform 的 run 状态、DAG、run 对比 API 和轻量展示页面。

## 数据边界

本项目视觉图像和 mask 几何特征来自 KICT Tunnel Crack Segmentation Dataset。机器人巡检过程中的时间、里程、环号、方位、`disease_id` 和跨巡检变化关系为仿真元数据。

当前系统使用 KICT 静态裂缝 mask 与仿真机器人巡检元数据构建端到端流程。
系统可以验证病害对象建模、跨巡检关联、增长分析和报告展示的工程闭环，但不能直接证明真实隧道病害长期演化规律。

由于 KICT 是静态公开图像数据集，本项目的跨时间增长分析主要用于验证监测流程和工程化表达能力，不能声称反映真实隧道病害长期演化规律，也不能替代现场工程检测结论。

## 项目目录结构

```text
data/
  simulated/                  # 仿真巡检表、KICT 几何特征和合并后的核心表

scripts/                      # 数据检查、特征提取、合并、报告和可视化脚本

outputs/
  visualizations/             # 增长分析和复检清单图表
  *.md                        # 阶段报告和检查报告

docs/                         # 运行流程、阶段总结、比赛/专利/软著材料

web_demo/                     # 本地 Web Dashboard 前端
web/                          # Multi-Agent 平台 Dashboard / DAG / Run 对比页面
web_app.py                    # 本地 Web 服务
run.py                        # 当前推荐的一键完整应用闭环入口
orchestrator/                 # Memory/Association Agent、DAG、run 管理和平台 API
```

## 核心脚本

| 脚本 | 作用 |
|---|---|
| `scripts/generate_simulation_tables.py` | 生成机器人巡检仿真表，包括巡检序列、病害实例、帧-病害映射和增长记录。 |
| `scripts/inspect_kict_dataset.py` | 检查 KICT `images/` 与 `masks/` 匹配关系，并生成 5 组预览图。 |
| `scripts/extract_kict_mask_features.py` | 从 KICT mask 中提取面积、bbox、中心点、mask 尺寸等几何特征。 |
| `scripts/merge_kict_with_simulation.py` | 将 KICT 图像/mask 路径和几何特征接入机器人巡检仿真表。 |
| `scripts/generate_engineering_report.py` | 生成按巡检和病害对象组织的工程化中文报告。 |
| `scripts/analyze_disease_growth.py` | 统计同一病害跨巡检的面积、风险和趋势变化。 |
| `scripts/generate_visualization_and_recheck_list.py` | 生成可视化图表、重点复检清单和阶段性 Markdown 报告。 |
| `run.py` | 当前推荐入口，作为 Orchestrator DAG 的薄封装运行完整 pipeline。 |

## 一键运行完整闭环

如果已经存在 `data/simulated/robot_kict_frame_records.csv`，可以直接运行：

```bash
python run.py --mode full_pipeline
```

等价 DAG 原生入口：

```bash
python orchestrator/run.py --dag config/dag.yaml
```

成功后会输出类似：

```text
engineering rows: 30
memory rows: 10
association rows: 30
growth rows: 10
recheck rows: 10
visual artifacts: 7
final report: outputs/final_project_report.md
```

完整执行链如下：

```text
robot_kict_frame_records.csv
-> disease_engineering_report.csv
-> disease_growth_results.csv
-> disease_memory_bank.csv
-> disease_association_records.csv
-> priority_recheck_list.csv
-> outputs/visualizations/*.png
-> outputs/final_project_report.md
```

## 从原始 KICT 数据重新生成流程

KICT 数据集目录需要包含：

```text
images/
masks/
```

如果需要从 KICT `images/` 和 `masks/` 重新生成全部中间数据，可按以下顺序运行：

```bash
python scripts/generate_simulation_tables.py
python scripts/inspect_kict_dataset.py --dataset-root /path/to/KICT
python scripts/extract_kict_mask_features.py --dataset-root /path/to/KICT
python scripts/merge_kict_with_simulation.py
python scripts/generate_engineering_report.py
python scripts/analyze_disease_growth.py
python scripts/generate_visualization_and_recheck_list.py
python run.py --mode full_pipeline
```

Windows 示例：

```powershell
python scripts/inspect_kict_dataset.py --dataset-root "C:/path/to/kict_sample"
python scripts/extract_kict_mask_features.py --dataset-root "C:/path/to/kict_sample"
```

更完整的步骤说明见 `docs/run_pipeline.md`。

## 主要输出文件

| 输出 | 说明 |
|---|---|
| `data/simulated/inspection_sequence.csv` | 机器人巡检帧序列仿真表。 |
| `data/simulated/disease_instances.csv` | 仿真病害对象表。 |
| `data/simulated/frame_disease_mapping.csv` | 图像帧与病害对象映射表。 |
| `data/simulated/disease_growth_records.csv` | 仿真增长记录表。 |
| `data/simulated/kict_mask_features.csv` | KICT mask 几何特征表。 |
| `data/simulated/robot_kict_frame_records.csv` | 融合 KICT 与仿真巡检元数据的核心表。 |
| `data/simulated/disease_engineering_report.csv` | 工程化病害描述表。 |
| `data/simulated/disease_growth_analysis.csv` | Web 兼容用跨巡检增长分析表。 |
| `data/simulated/disease_growth_results.csv` | 当前完整 pipeline 的标准增长分析输出。 |
| `data/simulated/disease_memory_bank.csv` | Disease Memory Bank，按 `disease_id` 汇总长期病害对象记忆。 |
| `data/simulated/disease_association_records.csv` | 当前完整 pipeline 的标准病害关联记录表。 |
| `data/simulated/association_records.csv` | 旧 orchestrator/Web 兼容用关联记录表。 |
| `data/simulated/priority_recheck_list.csv` | 重点复检清单。 |
| `outputs/disease_engineering_report.md` | 工程化病害描述报告。 |
| `outputs/disease_growth_analysis_report.md` | 增长变化分析报告。 |
| `outputs/recheck_list_report.md` | 重点复检清单报告。 |
| `outputs/visualization_report.md` | 可视化生成报告。 |
| `outputs/final_project_report.md` | 当前完整闭环最终项目报告，适合展示和答辩。 |
| `outputs/system_summary.md` | 系统闭环摘要。 |
| `outputs/key_insights.md` | 关键洞察和复检建议摘要。 |
| `outputs/visualizations/*.png` | 关注等级、增长趋势、风险变化、面积增长率、病害类型、里程风险图表。 |
| `outputs/visualizations/association_relationship_graph.png` | 病害对象与关联帧数量关系图。 |

## Web Dashboard

启动本地 Web 服务：

```bash
python web_app.py --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/
```

Web 页面当前展示：

1. 系统总览指标。
2. 工程化病害报告。
3. 跨巡检增长分析。
4. 重点复检清单。
5. 6 张可视化图表。
6. 单图检测/现场复核入口。
7. KICT 静态数据与仿真元数据边界说明。

Multi-Agent 平台页面：

```text
http://127.0.0.1:8000/platform
```

平台页面用于查看 orchestrator 的 run 状态、DAG 节点状态和 run 对比。它是工程管理辅助页面，不是病害检测主界面。

## 单图检测输出

单图检测仍保留原有分割复核能力，便于现场图片拖拽检查和模型结果对照。常见输出包括：

- `_single_mask.png`
- `_fused_mask.png`
- `_hybrid_mask.png`
- `_selected_mask.png`
- `_overlay.png`
- `_selected_overlay.png`
- `_uncertainty_heatmap.png`
- `_disagreement_heatmap.png`
- `_skeleton.png`
- `_report.json`

这里的融合、选择和不确定性结果用于辅助复核。若没有人工 GT 标注，页面中的 self-IoU / overlap 指标只能表示不同推理结果之间的一致性，是 not ground-truth mIoU，不能作为真实分割精度。

## 当前创新点

- 面向机器人巡检的病害对象级建模。
- Disease Memory Bank 批处理记忆表。
- 基于空间、面积、时间和风险的规则关联评分。
- DAG 多阶段工程闭环。
- 可视化报告和重点复检清单。

Association Agent 会输出 `spatial_distance_score`、`area_similarity_score`、`temporal_continuity_score`、`risk_similarity_score`、`association_score`、`confidence_level`、`match_type`、`candidate_count`、`top_candidate_ids`、`score_margin`、`conflict_reason` 和 `needs_manual_review`。其中 `disease_id` 只是辅助信息，不能一票决定高置信匹配。

## 模型与来源标记

Web 服务支持显式模型来源标记，方便区分旧基线与 SegFormer 结果：

```bash
python web_app.py --host 127.0.0.1 --port 8000 --model-source segformer
```

报告 JSON 中会保留 `mask_source`，常见值包括 `legacy_resnet50_fcn` 和 `segformer_b1`。其中 `legacy_resnet50_fcn` 主要作为旧版对照基线，`segformer_b1` 是当前更推荐的分割来源。

## 资料索引

项目中仍保留论文、专利、比赛和软著准备材料，便于老师或评审快速查看证据链：

- `docs/experiments/enhancement-evidence-summary.md`
- `docs/experiments/patent-ablation-summary.md`
- `docs/experiments/patent-case-pack.md`
- `docs/paper/confidence-review-outline.md`
- `docs/paper/claim-to-evidence-matrix.md`
- `docs/software-copyright/tunnel-defect-review-system.md`
- `docs/competition/cs-202613-requirements-matrix.md`
- `docs/competition/robot-spatiotemporal-monitoring.md`
- `docs/competition/civil-defect-evaluation.md`
- `docs/competition/multidomain-detector-notes.md`
- `docs/competition/technical-report-outline.md`
- `docs/competition/demo-script.md`
- `docs/competition/experiment-summary.md`
- `docs/competition/demo-case-pack.md`
- `docs/competition/report-template.md`
- `docs/local-setup-windows.md`

多领域巡检入口脚本为 `run_multidomain_inspection.py`。这些材料中的指标和结论需要结合“公开静态数据 + 仿真巡检元数据”的边界阅读，不能把仿真增长直接描述成真实线路长期增长。

## 当前限制

1. KICT 不是连续巡检数据。
2. 时间、里程、环号、方位是仿真生成。
3. `disease_id` 和跨巡检增长关系属于仿真验证。
4. 当前结果不代表真实隧道病害长期演化规律。
5. 当前重点是验证系统流程，而不是替代工程检测结论。
6. 如果用于论文、比赛或专利材料，需要明确标注“仿真验证”和“静态公开数据集”的边界。

## 后续扩展方向

1. 接入真实机器人连续巡检数据。
2. 接入真实模型预测结果和真实坐标定位。
3. 增加裂缝骨架长度、宽度变化分析。
4. 增加简单趋势预测模型。
5. 增加路线里程轴、筛选、搜索和病害详情页。
6. 导出 Word/PDF 工程报告。
7. 接入机器人里程计、IMU、SLAM 或轨道里程标定数据。

## 环境依赖

机器人巡检数据流程的轻量依赖见 `requirements.txt`：

```bash
pip install -r requirements.txt
```

SegFormer/mmseg 训练和旧单图分割能力属于额外模型环境，不是运行当前机器人巡检表格流程的必需条件。

当前完整 pipeline 已将可视化脚本改为 `stdlib + matplotlib`，不依赖 pandas，避免本地 `pandas/numpy` 二进制版本不兼容导致一键运行失败。
