# 隧道巡检病害分析系统 Artifact Contract

本文档定义当前机器人隧道巡检病害监测闭环中的核心 CSV / Markdown / 图表产物。目标是让数据生产者、消费者、字段含义和版本边界稳定下来，避免 pipeline 中出现隐性字段漂移。

## 版本规则

- 当前 artifact schema version: `v1`.
- 新增字段允许，但不能删除本文件列出的必需字段。
- 修改字段含义时必须提升 schema version，并同步更新 producer、consumer 和测试。
- `data/simulated/disease_growth_analysis.csv` 与 `data/simulated/association_records.csv` 为旧 Web / Orchestrator 兼容文件。
- 当前跨巡检增长结果基于 KICT 静态 mask + 仿真巡检元数据，不可描述为真实线路长期病害增长。

## 执行入口

| 入口 | 说明 |
|---|---|
| `python run.py --mode full_pipeline` | 根目录薄封装，实际调用 Orchestrator DAG。 |
| `python orchestrator/run.py --dag config/dag.yaml` | DAG 原生入口，默认运行 `full_pipeline` task。 |

## 核心产物

### robot_kict_frame_records.csv

路径：`data/simulated/robot_kict_frame_records.csv`

Producer：`scripts/merge_kict_with_simulation.py`

Consumer：`scripts/generate_engineering_report.py`

必需字段：

- `image_id`
- `inspection_id`
- `frame_id`
- `timestamp`
- `mileage_m`
- `mileage_text`
- `ring_id`
- `clock_direction`
- `disease_id`
- `disease_type`
- `kict_image_path`
- `kict_mask_path`
- `kict_area_px`
- `kict_bbox_x1`
- `kict_bbox_y1`
- `kict_bbox_x2`
- `kict_bbox_y2`
- `kict_center_x`
- `kict_center_y`
- `has_crack`

### disease_engineering_report.csv

路径：`data/simulated/disease_engineering_report.csv`

Producer：`scripts/generate_engineering_report.py`

Consumer：`scripts/analyze_disease_growth.py`, `run.py`, Web Dashboard

必需字段：

- `inspection_id`
- `disease_id`
- `disease_type`
- `frame_count`
- `start_frame`
- `end_frame`
- `start_time`
- `end_time`
- `start_mileage_m`
- `end_mileage_m`
- `start_mileage_text`
- `end_mileage_text`
- `start_ring`
- `end_ring`
- `main_clock_direction`
- `max_area_px`
- `mean_area_px`
- `total_area_px`
- `risk_level`
- `engineering_description`

枚举：

- `risk_level`: `低 | 中 | 高`

### disease_growth_results.csv

路径：`data/simulated/disease_growth_results.csv`

兼容副本：`data/simulated/disease_growth_analysis.csv`

Producer：`scripts/analyze_disease_growth.py`

Consumer：`MemoryAgent`, `scripts/generate_visualization_and_recheck_list.py`, Web Dashboard

必需字段：

- `disease_id`
- `disease_type`
- `inspection_count`
- `first_inspection`
- `last_inspection`
- `first_area_px`
- `last_area_px`
- `area_growth_px`
- `area_growth_rate`
- `first_risk_level`
- `last_risk_level`
- `risk_level_change`
- `growth_trend`
- `attention_level`
- `first_mileage_range`
- `last_mileage_range`
- `main_clock_direction`
- `growth_description`

枚举：

- `growth_trend`: `明显增长 | 轻微增长 | 基本稳定 | 面积减小 | 数据不足`
- `attention_level`: `重点关注 | 持续观察 | 常规记录 | 待补充巡检`

可信性说明：

- `growth_trend` 是基于仿真巡检元数据和 mask 面积规则生成的工程提示。
- 它不是结构安全结论，也不是真实线路长期演化证据。

### disease_memory_bank.csv

路径：`data/simulated/disease_memory_bank.csv`

Producer：`MemoryAgent`

Consumer：`AssociationAgent`, final report

必需字段：

- `memory_id`
- `memory_version`
- `disease_id`
- `disease_type`
- `source_record_count`
- `source_inspection_ids`
- `memory_update_mode`
- `memory_confidence`
- `memory_limit_note`
- `first_seen_inspection`
- `last_seen_inspection`
- `inspection_count`
- `total_seen_frames`
- `first_area_px`
- `last_area_px`
- `max_area_px`
- `area_growth_px`
- `area_growth_rate`
- `first_risk_level`
- `last_risk_level`
- `risk_level_change`
- `growth_trend`
- `attention_level`
- `main_clock_direction`
- `mileage_range`
- `representative_image_path`
- `representative_mask_path`
- `memory_description`

当前限制：

- v1 memory 是批量重建式 memory，`memory_update_mode=batch_rebuild`。
- `memory_confidence` 只表达当前仿真巡检元数据下的记录充分性，不代表真实长期跟踪置信度。
- 后续版本再引入 incremental update 和 conflict handling。

### disease_association_records.csv

路径：`data/simulated/disease_association_records.csv`

兼容副本：`data/simulated/association_records.csv`

Producer：`AssociationAgent`

Consumer：final report, Web Dashboard, artifact review

必需字段：

- `association_id`
- `inspection_id`
- `frame_id`
- `image_id`
- `disease_id`
- `memory_id`
- `association_status`
- `rule_basis`
- `association_score`
- `spatial_distance_score`
- `area_similarity_score`
- `temporal_continuity_score`
- `risk_similarity_score`
- `confidence_level`
- `match_type`
- `candidate_count`
- `top_candidate_ids`
- `score_margin`
- `conflict_reason`
- `needs_manual_review`
- `mileage_text`
- `clock_direction`
- `disease_type`
- `kict_area_px`
- `risk_level`
- `growth_trend`
- `kict_image_path`
- `kict_mask_path`

枚举：

- `association_status`: `matched | unmatched`
- `confidence_level`: `high | medium | low`
- `match_type`: `hard | soft | uncertain`

评分说明：

- `hard`: 同一 `disease_id` 命中，且空间、面积、时间、风险不存在明显冲突。
- `soft`: 通过空间距离、面积相似、时间连续、风险相似和 `disease_id` 辅助信息综合评分选择候选。
- `uncertain`: 没有候选达到最低阈值，或候选存在明显冲突，需人工复核。

约束：

- `disease_id` 不能一票决定 hard match。
- 若空间、面积、时间或风险存在明显冲突，应降级为 `uncertain` 或进入人工复核。
- `candidate_count`、`top_candidate_ids`、`score_margin` 和 `conflict_reason` 用于解释多候选竞争或低置信匹配。

### priority_recheck_list.csv

路径：`data/simulated/priority_recheck_list.csv`

Producer：`scripts/generate_visualization_and_recheck_list.py`

Consumer：Web Dashboard, final report

必需字段：

- `priority_rank`
- `disease_id`
- `disease_type`
- `attention_level`
- `growth_trend`
- `first_inspection`
- `last_inspection`
- `area_growth_rate`
- `last_mileage_range`
- `main_clock_direction`
- `recheck_reason`
- `recheck_suggestion`

## 报告与图表

| Artifact | Producer | Consumer |
|---|---|---|
| `outputs/final_project_report.md` | `run.py --mode full_pipeline` | 展示 / 答辩 |
| `outputs/system_summary.md` | `run.py --mode full_pipeline` | 项目交接 |
| `outputs/key_insights.md` | `run.py --mode full_pipeline` | 老师快速阅读 |
| `outputs/visualizations/*.png` | `scripts/generate_visualization_and_recheck_list.py` | Web Dashboard / 报告 |
| `outputs/visualizations/association_relationship_graph.png` | `run.py --mode full_pipeline` | 关联关系展示 |

## 边界声明

本项目当前数据来源是 KICT 静态裂缝 mask 和机器人巡检仿真元数据。系统可以证明工程闭环、数据结构和展示能力，但不能直接证明真实隧道病害在时间序列上的实际增长规律。
