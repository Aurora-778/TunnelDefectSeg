# 隧道巡检病害分析系统 Artifact Contract

本文档定义当前机器人隧道巡检病害监测闭环中的核心 CSV / Markdown / 图表产物。目标是让数据生产者、消费者、字段含义和版本边界稳定下来，避免 pipeline 中出现隐性字段漂移。

## 版本规则

- 当前 artifact schema version: `v1`.
- 新增字段允许，但不能删除本文件列出的必需字段。
- 修改字段含义时必须提升 schema version，并同步更新 producer、consumer 和测试。
- `data/simulated/disease_growth_analysis.csv` 与 `data/simulated/association_records.csv` 为旧 Web / Orchestrator 兼容文件。
- 当前跨巡检增长结果基于 KICT 静态 mask + 仿真巡检元数据，不可描述为真实线路长期病害增长。
- `memory_version` 必须以 `v` 开头，例如 `v1`、`v1.0`、`v2`。
- bool 字段允许 `true`、`false`、`True`、`False`、`0`、`1`；输出建议统一为小写 `true` / `false`。
- 分数类字段默认要求 `0 <= score <= 1`；`score_margin` 表示候选差距，只要求 `score_margin >= 0`。

## 执行入口

| 入口 | 说明 |
|---|---|
| `python run.py --mode full_pipeline` | 根目录薄封装，实际调用 Orchestrator DAG。 |
| `python orchestrator/run.py --dag config/dag.yaml` | DAG 原生入口，默认运行 `full_pipeline` task。 |
| `python scripts/run_progressive_inspection_evaluation.py` | 时间递进评估入口：历史 memory 匹配当前巡检，再增量更新 memory。 |

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
- `measurement_basis`
- `claim_level`
- `comparability_status`
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
- `claim_level=baseline_only` 表示只有单次巡检基线；`rule_evidence_only` 表示仅有规则证据；强结论需要人工复核或真实可比数据支撑。

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
- `last_area_px`
- `max_area_px`
- `growth_trend`
- `attention_level`
- `mileage_range`
- `requires_manual_review`
- `memory_description`

当前限制：

- 主 pipeline 的 v1 memory 仍是批量重建式 memory，`memory_update_mode=batch_rebuild`。
- 渐进式评估脚本会使用 `memory_update_mode=incremental_update`，只从历史 memory 与当前巡检关联结果更新记忆库。
- `memory_confidence` 只表达当前仿真巡检元数据下的记录充分性，不代表真实长期跟踪置信度。
- conflict handling 当前通过 `requires_manual_review`、`candidate_count`、`score_margin` 和 `conflict_reason` 暴露给人工复核。

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
- `label_disease_id`
- `memory_id`
- `association_status`
- `rule_basis`
- `use_disease_id_score`
- `association_mode`
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
- `geometry_feature_available`
- `geometry_limit_note`

兼容 / optional 字段：

- `bbox_fields_present`
- `geometry_score_applied`
- `bbox_center_distance_score`
- `bbox_size_similarity_score`
- `aspect_ratio_similarity_score`
- `shape_proxy_score`

Progressive evaluation 专用字段：

- `matched_disease_id`：只在 progressive no-id / with-id evaluation artifacts 中必需，用于评估匹配结果，不作为主 pipeline matching 输入。

枚举：

- `association_status`: `matched | unmatched`
- `confidence_level`: `high | medium | low`
- `match_type`: `hard | soft | uncertain`
- `association_mode`: `no_id | with_id_upper_bound`

评分说明：

- 主 pipeline 默认 `use_disease_id_score=false`、`association_mode=no_id`。
- `label_disease_id` 是数据集 / 仿真标签，不参与主 pipeline 的真实 matching 打分。
- `with_id_upper_bound` 只用于 progressive evaluation 的上界 / sanity check，不进入主 DAG。
- `hard`: 候选在 no-id 评分下达到高置信匹配要求，不能由 `label_disease_id` 一票决定。
- `soft`: 通过空间距离、面积相似、时间连续、风险相似等非 ID 证据综合评分选择候选。
- `uncertain`: 没有候选达到最低阈值，或候选存在明显冲突，需人工复核。
- 渐进式评估中 `disease_id` 只作为评估标签，匹配打分、`hard` 判定和解释文本都会禁用 `disease_id`。

约束：

- `label_disease_id` 不能一票决定 hard match。
- `score_margin >= 0`，不强制 `score_margin <= 1`。
- 若空间、面积、时间或风险存在明显冲突，应降级为 `uncertain` 或进入人工复核。
- `candidate_count`、`top_candidate_ids`、`score_margin` 和 `conflict_reason` 用于解释多候选竞争或低置信匹配；`top_candidate_ids` 保留兼容字段名，但内容应使用 `memory_id:score` 作为展示标签，避免把评估标签 `disease_id` 误解为 progressive scoring 输入。

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
| `outputs/association_evaluation_report.md` | `scripts/run_progressive_inspection_evaluation.py` | Association baseline / ablation 评估 |

### progressive_evaluation_manifest.json

路径：`data/simulated/progressive/progressive_evaluation_manifest.json`

Producer：`scripts/run_progressive_inspection_evaluation.py`

用途：

- 记录每轮 `history_inspections` 和 `query_inspection`。
- 顶层 `source_dataset` 记录完整源表路径。
- 每轮 `allowed_inputs` 只记录该轮允许读取的 history / query / memory artifacts，不能包含 `source_dataset`。
- 每轮必须记录 `association_records`、`no_id_association_records`、`with_id_association_records`、`memory_before`、`memory_after`。
- 记录 same disease_id、nearest mileage、area only、weighted score no id 的 baseline / ablation 指标。

可信性说明：

- 该入口用于检查 temporal leakage：匹配当前巡检时不能读取未来巡检帧或未来聚合结果。
- 当前评估标签仍来自仿真 `disease_id`，不是现场人工标注的真实 identity benchmark。

## 边界声明

本项目当前数据来源是 KICT 静态裂缝 mask 和机器人巡检仿真元数据。系统可以证明工程闭环、数据结构和展示能力，但不能直接证明真实隧道病害在时间序列上的实际增长规律。
