# TunnelDefect 工程 Agent 化 CEPlan v6（最终执行版）

> 文档性质：工程实施计划，不是代码实现  
> 仓库基线：Git `main@7203da7`；检查时存在与本计划无关的 Web/视频本地 WIP，实施时必须隔离，不得覆盖或提交  
> 本轮范围：只修订 CEPlan 与 Review 关闭表；不修改仓库代码、不运行 Phase A 开发、不改变算法阈值、不重新生成正式产物  
> 适用阶段：Phase 0 合同收束与 Phase A 最小工程 Agent

---

# 0. 仓库检查摘要

当前主 DAG 为 `engineering_report → growth_analysis → memory/association → visualization → final_report`；唯一执行器是 `orchestrator/executor.py::DAGExecutor`，Run 管理由 `orchestrator/runs/manager.py::RunManager` 完成，Run ID 使用 `run_NNN` 命名模式。`orchestrator/state/store.py` 仅提供简单 JSON checkpoint，尚无 CAS/WAL。history-only Association 会为每轮生成 `memory_before_query.csv` 和 manifest，可作为历史 Memory Snapshot 血缘。Growth、Memory、Visualization、FinalReport 当前均直接生成可读报告，尚未读取 ClaimDecision。Prepared Dataset 已有严格 `require_inference_ready(Path)` Gate 和原子发布实现，可复用其设计经验。

---

# 1. 真实仓库事实基线

实施前必须以本节为事实基线；仓库发生变化时必须重新检查，不能机械照抄。

## 1.1 当前 DAG 与配置

真实文件：

```text
config/dag.yaml
```

当前 DAG：

```text
engineering_report
        ↓
growth_analysis
        ├────────→ memory
        └────────→ association
                          ↓
                     visualization
                          ↓
                     final_report
```

当前精确依赖：

```yaml
engineering_report:
  deps: []

growth_analysis:
  deps: [engineering_report]

memory:
  deps: [growth_analysis]

association:
  deps: [growth_analysis]

visualization:
  deps: [growth_analysis, association]

final_report:
  deps: [memory, association, visualization]
```

当前所有节点：

```text
retries: 2
cache: false
```

## 1.2 当前执行组件

|职责|真实位置|当前行为|
|---|---|---|
|项目入口|`run.py`|`run_full_pipeline()` 直接构建 DAG 并实例化 `DAGExecutor`|
|DAG 构建|`orchestrator/dag/builder.py`|`Task`、`build_dag()`|
|唯一任务执行器|`orchestrator/executor.py`|`DAGExecutor`|
|Agent 注册|`orchestrator/registry.py`|`AgentRegistry`、`build_default_registry()`|
|Run 管理|`orchestrator/runs/manager.py`|`RunManager`，通过目录扫描分配 `run_NNN`|
|Checkpoint|`orchestrator/state/store.py`|`save_checkpoint()`、`load_checkpoint()`、`make_state()`|
|每 Run 状态|`runs/<run_id>/state.json`|由 `DAGExecutor._checkpoint()` 写入|
|全局兼容状态|`orchestrator/state/run_state.json`|同样由 `_checkpoint()` 写入|
|Run metadata|`runs/<run_id>/metadata.json`|`RunManager.update_metadata()` 独立写 `status`|
|Context 版本|`runs/<run_id>/context_vN.json`|`RunManager.save_context_version()` 写入|

当前 `DAGExecutor.__init__()` 默认会创建 Run。它已经支持传入存在的：

```text
run_id
```

因此 Phase A 的 Controller 可以在取得全局锁并创建 Run 后，将该 `run_id` 传给 `DAGExecutor`，避免 Executor 再分配第二个 Run。

## 1.3 当前 Association 真实语义与字段

真实文件：

```text
orchestrator/agents/association_agent.py
orchestrator/history_only_association.py
```

生产边界：

```text
history_only: true
association_mode: no_id
use_disease_id_score: false
```

Association 是基于以下规则证据进行的候选评分：

```text
空间距离
面积相似度
时间连续性
风险相似度
```

它不是现实世界同一病害身份认证。

当前 Association CSV 真实字段包括：

```text
association_id
inspection_id
frame_id
image_id
label_disease_id
history_inspection_ids
memory_id
association_status              # matched / unmatched
rule_basis
use_disease_id_score
association_mode
association_score
spatial_distance_score
area_similarity_score
temporal_continuity_score
risk_similarity_score
confidence_level
match_type                      # hard / soft / uncertain
candidate_count
top_candidate_ids
score_margin
conflict_reason
needs_manual_review
bbox_fields_present
geometry_score_applied
geometry_feature_available
geometry_limit_note
mileage_text
clock_direction
disease_type
kict_area_px
risk_level
growth_trend
kict_image_path
kict_mask_path
```

注意：

```text
growth_trend
```

只是从候选 Memory 行复制的内部规则字段，不能直接作为正式 Claim。

## 1.4 history-only Memory Snapshot 血缘

`orchestrator/history_only_association.py` 每轮生成：

```text
data/simulated/main_progressive/round_NNN/
├─ history_frames.csv
├─ history_engineering_report.csv
├─ history_growth_analysis.csv
├─ memory_before_query.csv
├─ query_frames.csv
├─ association_records.csv
├─ memory_after_query.csv
├─ memory_before_query_report.md
├─ memory_before_query_summary.md
└─ ...
```

并生成：

```text
data/simulated/main_progressive/association_manifest.json
```

Manifest 的每轮信息包含：

```text
round_index
query_inspection
history_inspection_ids
query_frame_count
query_frames
mode
memory_before
association_records
memory_after
```

因此 Phase A 可通过以下链路获得可信的历史比较来源：

```text
Association row.inspection_id
→ association_manifest 对应 round.query_inspection
→ round.memory_before
→ memory_before_query.csv 中 row.memory_id
```

该来源是：

```text
historical memory snapshot
```

不是精确历史原始 Observation。

## 1.5 当前 Growth 与报告

真实节点：

```text
orchestrator/agents/growth_analysis_agent.py::GrowthAnalysisAgent
scripts/analyze_disease_growth.py
```

当前一次性生成：

```text
data/simulated/disease_growth_results.csv
data/simulated/disease_growth_analysis.csv
outputs/disease_growth_analysis_report.md
outputs/disease_growth_analysis_summary.md
```

当前 Growth CSV 包含：

```text
area_growth_px
area_growth_rate
risk_level_change
growth_trend
attention_level
claim_level
comparability_status
growth_description
```

现有数值公式必须保留，但正式报告发布权必须从 `GrowthAnalysisAgent` 中分离。

## 1.6 当前 Memory 与报告

真实节点：

```text
orchestrator/agents/memory_agent.py::MemoryAgent
```

当前 Batch Memory 同时生成：

```text
data/simulated/disease_memory_bank.csv
outputs/memory_agent_report.md
outputs/disease_memory_bank_summary.md
```

Memory CSV 含有：

```text
area_growth_px
area_growth_rate
risk_level_change
growth_trend
attention_level
comparability_status
memory_description
```

`memory_description` 在可比较分支中会输出：

```text
面积变化率
呈……趋势
```

因此 Memory 人类可读报告与描述字段必须纳入 Claim 发布合同，不能成为 Claim Gate 的旁路。

history-only round 中的：

```text
memory_before_query_report.md
memory_after_query_report.md
```

属于内部审计产物，但也必须采用中性内部证据模板，禁止输出未门控方向性结论。

## 1.7 当前 Visualization、Recheck 与 FinalReport

真实节点：

```text
orchestrator/agents/visualization_agent.py::VisualizationAgent
orchestrator/agents/final_report_agent.py::FinalReportAgent
```

Visualization 当前直接读取：

```text
disease_engineering_report.csv
disease_growth_results.csv
disease_association_records.csv
```

并通过：

```text
scripts/generate_visualization_and_recheck_list.py
```

生成：

```text
data/simulated/priority_recheck_list.csv
outputs/visualizations/
outputs/visualization_report.md
outputs/recheck_list_report.md
outputs/visualization_summary.md
```

当前没有独立 `recheck` DAG 节点。

FinalReport 当前直接读取：

```text
engineering_report
growth_results
memory_bank
association_records
recheck_list
visualization_dir
```

并生成：

```text
outputs/final_project_report.md
outputs/system_summary.md
outputs/key_insights.md
```

仅改变 DAG `deps` 不足以完成 Claim Gate；这些 Agent 的输入合同必须实际增加并验证：

```text
comparison_evidence
claim_decision
```

## 1.8 Prepared Dataset 真实合同

真实脚本：

```text
scripts/prepare_real_inspection_pilot.py
```

正式输出：

```text
observation_records.csv
frame_records.csv
preparation_manifest.json
```

生产 Gate：

```python
require_inference_ready(manifest_path: Path)
```

它已经校验：

```text
data_contract_version
integrity_scope
artifact_set_complete
inference_ready
readiness_reasons
observation_records path/size/SHA-256/row count
frame_records path/size/SHA-256/row count
CSV Schema
行级一致性
检查期间文件稳定性
staging/backup recovery 数据
```

Manifest 真实结构还包括：

```text
source_metadata.path/size_bytes/sha256
source_artifacts[*].role/path/size_bytes/sha256
outputs.observation_records
outputs.frame_records
```

Phase A 必须直接复用现有 Gate，不能重新发明一个较弱的验证器。

## 1.9 当前 Artifact Isolation

真实文件：

```text
tests/conftest.py
tests/test_test_artifact_isolation.py
```

现有能力：

```text
PROTECTED_ARTIFACTS
artifact_snapshot()
artifact_manifest_diff()
session autouse guard
```

已检测：

```text
added
removed
modified
空目录变化
```

Phase A 应扩展该守卫，而不是另建重复框架。

---

# 2. 严格边界

## 2.1 禁止修改

```text
AssociationAgent 核心评分公式
NO_ID_MATCH_THRESHOLD
NO_ID_SOFT_THRESHOLD
NO_ID_MARGIN_THRESHOLD
history_only=true
association_mode=no_id
use_disease_id_score=false
weighted_no_id 生产语义
Memory Bank 核心聚合语义
Growth 现有数值计算公式
Benchmark Ground Truth
with-id upper-bound 定位
全局 one-to-one assignment 边界
```

## 2.2 允许修改

```text
任务请求合同
输入 Readiness
DAG 节点和报告发布顺序
Comparison Evidence
Claim Gate
报告渲染输入合同
Memory 中性内部报告模板
正式报告 Staging
Publication Transaction
Active Run Lock
StateStore API
CAS / WAL
Path Safety
Artifact Isolation
Semantic Snapshot
CLI 兼容入口
```

## 2.3 禁止引入

```text
LangChain
AutoGen
多 Agent 对话
向量数据库
Redis
Celery
数据库
Kubernetes
LLM 自动决策
自动调参
Hungarian matching
全局目标分配
视觉 embedding
SLAM
```

## 2.4 Phase A 必须分段交付

Phase A 不允许作为一次大提交实施，固定拆为：

```text
Phase A1: Comparison Evidence + Claim Gate + 受门控 Run-local 报告
Phase A2: Staging + Publication Transaction + Manifest 恢复
Phase A3: 双入口 Controller + Active Run Lock + Canonical State/CAS/WAL
```

边界：

- A1 只生成 `runs/<run_id>/artifacts/` 和 `runs/<run_id>/staging/`，不更新正式 `outputs/`。
- A2 先以 sandbox transaction 测试实现，未进入 A3 前不得接管 legacy CLI 的正式发布。
- A3 验收后，Prepared 与 Legacy 入口才共同启用锁、StateStore、现有 DAGExecutor 和 Publication。
- 每一段必须独立 review、通过专项测试和 Artifact Isolation；前一段未关闭 P0/P1 时不得进入下一段。

### 2.4.1 A1 Run-local 输出隔离合同

A1 不得在仓库根目录直接运行仍指向正式路径的 legacy full pipeline。A1 的专项执行器测试必须通过现有 Agent 的显式输出参数或最小兼容开关，将本轮所有可写路径覆盖到当前 Run：

```text
runs/<run_id>/work/
├─ disease_engineering_report.csv
├─ disease_growth_results.csv
├─ disease_memory_bank.csv
├─ disease_association_records.csv
└─ main_progressive/

runs/<run_id>/artifacts/
├─ comparison_evidence.csv
├─ comparison_evidence_manifest.json
└─ claim_decision.json

runs/<run_id>/staging/
└─ 受 Claim Gate 控制的人类可读报告与可视化
```

固定映射：

|现有节点|A1 允许输出|A1 禁止输出|
|---|---|---|
|Engineering|`runs/<run_id>/work/disease_engineering_report.csv`；内部中性 Markdown 可写 Staging|`data/simulated/*`、正式 `outputs/*`|
|Growth|`runs/<run_id>/work/disease_growth_results.csv`；受门控报告写 Staging|旧 Growth Markdown/Summary 正式路径|
|Memory|`runs/<run_id>/work/disease_memory_bank.csv`；旧 `_write_reports()` 关闭，受门控报告写 Staging|正式 Memory 报告路径|
|Association|`runs/<run_id>/work/disease_association_records.csv` 和 Run-local `main_progressive/`|正式 Association CSV 和正式 progressive 目录|
|Visualization/Recheck|只读取 Run-local Evidence/ClaimDecision，全部写 Staging|正式图片、正式 CSV、正式报告|
|FinalReport|只读取 Run-local/ Staging 当前 Run 产物，写 `staging/final_project_report.md` 等|正式 Final Report/Summary/Insights|

A1 只增加路径覆盖和报告写入模式，不改变任何算法公式、阈值或 DAG 依赖。默认 legacy 参数在 A1 期间保持原行为；A1 验收只能在 `tmp_path` 或临时项目副本中运行，不得以默认路径执行。A3 接管前，仓库根目录的 `data/simulated/`、`outputs/` 和正式 progressive 目录在 A1 测试前后必须满足 added/removed/modified 均为空。

上述映射是 A1 的组件合同，不是第二份 DAG。A1 sandbox 测试只允许直接实例化组件并验证输入输出合同，不得宣称已经完成 DAG 集成，也不得创建临时业务 DAG 配置。到 A3 时，最终依赖顺序一次性写入现有 `config/dag.yaml`，路径覆盖由现有 Agent 输入参数和同一份运行上下文完成；禁止新增 Executor、Registry 或并行编排配置。

A1 的激活边界固定为：

```text
默认 legacy CLI / run.py
→ 不注册、不调度 A1 新节点
→ 不启用 Run-local report mode
→ 行为与 A1 前完全一致

execution_profile=phase_a1_sandbox
AND project_root 为 tmp_path/临时项目副本
→ 允许直接实例化 A1 节点并使用 Run-local 路径覆盖

其他情况
→ 拒绝启用 A1 模式
```

A1 提交不得修改共享 `config/dag.yaml` 或 `orchestrator/registry.py` 来激活新节点；它只实现可直接测试的 Agent/renderer、必要的显式输出参数和 Fail Closed sandbox profile。A3 才把已经验收的节点注册到唯一 DAG/Registry，并同时接入统一 Run-local resolver、锁和 Publication。这样 A1 合入主分支后，默认 `python run.py --mode full_pipeline` 不会出现“新 Claim 节点已启用但旧正式输出仍直写”的半迁移状态。

---

# 3. 唯一 Phase A DAG

## 3.1 最终 DAG

```text
engineering_report
        ↓
growth_analysis                      # 保留现有数值公式；只产出结构化数据
        ├──────────────→ memory      # 保留 Memory CSV 聚合；内部报告必须中性
        └──────────────→ association
                                  ↓
                         comparison_evidence
                                  ↓
                              claim_gate
                         ┌────────┼────────────┐
                         ↓        ↓            ↓
                  growth_report memory_report visualization
                                                   │
                                                   └─ 内部生成 recheck
                         └────────────┬──────────────┘
                                      ↓
                                 final_report
                                      ↓
                         publication_validation
                                      ↓
                           publication_commit
                                      ↓
                               COMPLETED
```

其中：

```text
publication_validation
publication_commit
```

是 Controller 的发布阶段，不要求注册为业务 Agent。

## 3.2 精确依赖

```yaml
engineering_report:
  deps: []

growth_analysis:
  deps: [engineering_report]

memory:
  deps: [growth_analysis]

association:
  deps: [growth_analysis]

comparison_evidence:
  deps: [engineering_report, growth_analysis, association]

claim_gate:
  deps: [comparison_evidence]

growth_report:
  deps: [comparison_evidence, claim_gate]

memory_report:
  deps: [memory, claim_gate]

visualization:
  deps: [association, comparison_evidence, claim_gate, growth_report]

final_report:
  deps:
    - memory
    - memory_report
    - association
    - comparison_evidence
    - claim_gate
    - growth_report
    - visualization
```

## 3.3 节点职责

### `growth_analysis`

继续计算现有结构化 CSV 数值，保持公式不变。

Phase A 后不得再直接拥有正式 Growth Markdown 的发布权。

### `memory`

继续生成：

```text
disease_memory_bank.csv
```

保持聚合字段语义。

其内部运行报告只能使用：

```text
internal_neutral
```

模板，明确“内部规则证据，非正式工程结论”，并屏蔽方向性措辞。

### `comparison_evidence`（拟新增）

从：

```text
当前 Engineering/Growth 记录
Association CSV
Association Manifest
对应 round 的 memory_before_query.csv
```

建立：

```text
current observation
vs.
historical memory snapshot
```

的中性证据。

### `claim_gate`（拟新增）

只消费已有证据，生成逐记录 ClaimDecision。

禁止重新计算 Association 分数。

### `growth_report`（拟新增）

读取 Comparison Evidence 与 ClaimDecision，生成正式 Growth 报告到 Run Staging。

### `memory_report`（拟新增）

读取 Memory CSV 与 ClaimDecision，生成正式 Memory 报告到 Run Staging。

### `visualization`

实际读取并验证 ClaimDecision，生成受门控图表、复检清单和报告到 Run Staging。

### `final_report`

实际读取：

```text
claim_decision
comparison_evidence
growth_report
memory_report
visualization/recheck
```

生成 Staging 中的最终报告。

---

# 4. Association 支持证据与身份等级

## 4.1 禁止身份升级

Phase A 删除以下语义：

```text
same_object_confirmed
association_identity_state=accepted
```

统一使用：

```text
association_supported_pair
identity_evidence_state
```

## 4.2 身份证据状态

```text
association_supported
association_rejected
association_pending_review
association_invalid
association_not_applicable
human_verified
ground_truth_verified
```

Phase A 只能自动生成前五种。

## 4.3 状态映射

根据真实 Association 字段：

```text
association_status=matched
AND needs_manual_review=false
AND memory_id 非空
AND association_mode=no_id
AND use_disease_id_score=false
→ association_supported

association_status=unmatched
→ association_rejected

needs_manual_review=true
→ association_pending_review

Association 文件缺失
或 Schema 非法
或 Manifest 无对应 round
或 memory_before 不存在
或 memory_id 在 snapshot 中不唯一
或来源 Hash 不匹配
→ association_invalid

当前记录属于 Manifest 明确标记的 baseline/current-only 分支
AND 该分支按合同不产生 Association query
→ association_not_applicable
```

## 4.4 语义边界

```text
association_supported
```

只表示：

> 当前 history-only no-id 规则系统支持将该当前观测与该历史 Memory Snapshot 作为候选比较对。

它不表示：

```text
现实中的同一病害已确认
身份 Ground Truth
人工审核通过
跨轮配准完成
```

## 4.5 报告用语

当状态为 `association_supported` 时，只允许：

```text
基于当前规则关联候选……
在当前关联假设下……
根据 history-only no-id 关联结果……
当前证据支持候选比较，但不构成真实身份确认……
```

禁止：

```text
同一病害对象
该病害已确认增长
该裂缝持续扩大
```

只有：

```text
human_verified
ground_truth_verified
```

才允许无附加限制地使用“同一病害对象”。

---

# 5. Claim Capability

统一 Capability：

```text
static_descriptive_audit
descriptive_difference_claim
directional_change_claim
physical_quantity_change_claim
multi_timepoint_pattern_claim
prediction_claim
```

统一状态：

```text
allowed
allowed_with_limits
blocked
not_applicable
```

## 5.1 `static_descriptive_audit`

适用于单次观测、未匹配记录或待审核记录。

允许：

```text
当前面积、长度、像素数
当前风险等级
静态位置和 bbox
```

禁止：

```text
变化
增加
减少
稳定
趋势
恶化
改善
```

## 5.2 `descriptive_difference_claim`

允许描述前后数值差和相对差。

还必须满足：

```text
current_comparability_status = verified_comparable
previous_comparability_status = verified_comparable
comparison_comparability_status = verified_comparable
observation_source 已声明
metric 一致
measurement_method 一致
```

若任一侧不可纵向比较，原始数值只能进入 `static_descriptive_audit`，不得升级为 Difference Claim。

当身份仅为：

```text
association_supported
```

时，最高为：

```text
allowed_with_limits
```

只有 `human_verified` 或 `ground_truth_verified` 才可为无条件 `allowed`。

## 5.3 `directional_change_claim`

允许：

```text
相对增加
相对减少
方向性变化
```

还必须满足：

```text
至少两个有效时点
metric 一致
measurement_method 一致
difference_valid=true
temporal_order_valid=true
registration_status=registered
needs_manual_review=false
current_comparability_status=verified_comparable
previous_comparability_status=verified_comparable
comparison_comparability_status=verified_comparable
```

当身份仅为规则关联时，最高为：

```text
allowed_with_limits
```

物理尺度不是相对方向变化的必要条件。

## 5.4 `physical_quantity_change_claim`

Phase A 固定：

```text
blocked
```

原因：第一版 Comparison Evidence 只定义 `inspection_level_max_mask_area_px`，没有生成经标定换算的当前/历史物理量值。仅有 `physical_scale_calibrated=true` 不能自动把像素面积转换成物理量。

## 5.5 `multi_timepoint_pattern_claim`

Phase A 固定：

```text
blocked
```

原因：第一版证据模型只有“当前观测 vs. 一个历史 Memory Snapshot”，没有三个独立时点的结构化 Observation 列表，因此不能证明多时点模式。

继续禁止：

```text
长期趋势
持续恶化
未来会增长
```

## 5.6 `prediction_claim`

Phase A 固定：

```text
blocked
```

---

# 6. Claim Policy 机器规则

建议配置：

```yaml
claim_policy:
  schema_version: claim_policy_v4

  states:
    - allowed
    - allowed_with_limits
    - blocked
    - not_applicable

  static_descriptive_audit:
    allowed:
      when:
        - evidence_schema_valid == true
        - current_record_valid == true
        - current_observation_source_declared == true
        - current_observation_source in observation_source_enum
        - current_comparability_status in comparability_status_enum

  descriptive_difference_claim:
    allowed:
      when:
        - identity_evidence_state in [human_verified, ground_truth_verified]
        - current_comparability_status == verified_comparable
        - previous_comparability_status == verified_comparable
        - comparison_comparability_status == verified_comparable
        - current_observation_source_declared == true
        - previous_observation_sources_declared == true
        - valid_timepoint_count >= 2
        - metric_consistent == true
        - measurement_method_consistent == true
        - difference_valid == true

    allowed_with_limits:
      when:
        - identity_evidence_state == association_supported
        - current_comparability_status == verified_comparable
        - previous_comparability_status == verified_comparable
        - comparison_comparability_status == verified_comparable
        - current_observation_source_declared == true
        - previous_observation_sources_declared == true
        - valid_timepoint_count >= 2
        - metric_consistent == true
        - measurement_method_consistent == true
        - difference_valid == true

  directional_change_claim:
    allowed:
      when:
        - descriptive_difference_claim == allowed
        - identity_evidence_state in [human_verified, ground_truth_verified]
        - registration_status == registered
        - temporal_order_valid == true

    allowed_with_limits:
      when:
        - descriptive_difference_claim == allowed_with_limits
        - identity_evidence_state == association_supported
        - registration_status == registered
        - temporal_order_valid == true
        - needs_manual_review == false
        - comparison_comparability_status == verified_comparable

    does_not_require:
      - physical_scale_calibrated

  physical_quantity_change_claim:
    blocked:
      reason: PHASE_A_NO_PHYSICAL_QUANTITY_EVIDENCE

  multi_timepoint_pattern_claim:
    blocked:
      reason: PHASE_A_NO_THREE_TIMEPOINT_EVIDENCE

  prediction_claim:
    blocked:
      reason: PHASE_A_NO_VALIDATED_PREDICTION_MODEL
```

硬规则：

```text
association_rejected
→ 仅 static_descriptive_audit 可允许

association_pending_review
→ Phase A 仅 static_descriptive_audit 可允许

association_invalid
→ Claim Gate FAIL CLOSED

association_not_applicable
→ 仅 static_descriptive_audit 可允许

current_observation_source 未声明
→ Claim Gate FAIL CLOSED

current_observation_source 缺失、为空或存在未知枚举
→ Evidence INVALID
→ Claim Gate FAIL CLOSED

previous_observation_sources 缺失、类型错误、存在未知枚举，或在存在历史 Memory Snapshot 时为空
→ Evidence INVALID
→ Claim Gate FAIL CLOSED

previous_entity_type=not_applicable 且 previous/comparison comparability 均为 insufficient_history
→ previous_observation_sources=[] 合法

current_comparability_status / previous_comparability_status / comparison_comparability_status 缺失、为空或存在未知枚举
→ Evidence INVALID
→ Claim Gate FAIL CLOSED
→ 不得按“非 verified”降级为 Static Audit

previous_observation_sources 未声明
→ descriptive / directional 全部 blocked

三个 comparability 字段均通过 required/enum 校验，且 current_comparability_status != verified_comparable
或 previous_comparability_status != verified_comparable
或 comparison_comparability_status != verified_comparable
→ 仅 static_descriptive_audit 可允许

registration_status=not_verified
→ directional / physical / pattern 全部 blocked

physical_scale_calibrated=false
→ 不自动阻断 descriptive difference；Phase A 的 physical quantity 无论该值为何均固定 blocked
```

---

# 7. Memory Snapshot Comparison Evidence

## 7.1 Phase A 唯一比较模型

```text
current observation
vs.
historical memory snapshot
```

存在历史比较对象时固定：

```text
previous_entity_type = memory_snapshot
```

不得声明为：

```text
observation_to_observation
```

首次观测、unmatched 或 association_rejected 且不存在历史 Memory Snapshot 时使用：

```text
previous_entity_type = not_applicable
previous_observation_sources = []
previous_comparability_status = insufficient_history
comparison_comparability_status = insufficient_history
difference_valid = false
```

该分支只能形成当前观测的 Static Audit，不得伪造 `memory_id` 或历史来源。

## 7.2 数据定位流程

Comparison Evidence 必须以当前 Run 的 Engineering/Frame records 为主表，Association 只能作为可选左连接输入。禁止以 Association CSV 为主表，否则 header-only baseline 会被静默遗漏。

固定流程：

```text
1. 读取并验证当前 Engineering/Frame records，按中性 current observation 复合键建立唯一主表；
2. 读取 association_manifest.json，识别 baseline rounds 与 query rounds；
3. 按 `(inspection_id, frame_id, image_id, current_observation_id)` 左连接 Association query；
4. 对 baseline/current-only、matched、invalid 三条分支分别处理；
5. 仅 matched 分支读取对应 round.memory_before，并根据 memory_id 定位唯一 Memory 行；
6. 生成当前静态 Evidence 或历史比较 Evidence；
7. 保存所有来源 Hash 和分支类型。
```

三条分支固定为：

### Baseline / Current-only

```text
Manifest 明确标记为 baseline round，且该 round 合同规定不产生 query Association
→ Association 行缺失是合法状态
→ identity_evidence_state = association_not_applicable
→ previous_entity_type = not_applicable
→ 只生成当前观测 Static Audit

Association 行存在且 association_status=unmatched
→ identity_evidence_state = association_rejected
→ previous_entity_type = not_applicable
→ memory_id 为空是合法状态
→ 只生成当前观测 Static Audit
```

### Matched

```text
association_status=matched
→ Association 行、round、memory_before 和唯一 memory_id 全部必须存在且 Hash 一致
→ previous_entity_type = memory_snapshot
→ 按 Claim Policy 判断是否允许 Difference/Directional Claim
```

### Invalid

若：

```text
非 baseline query record 缺少应有的 Association 行
Association query composite key 重复或无法连接当前主表
matched 记录的 round 不存在
matched 记录的 memory_before 不存在
matched 记录的 memory_id 为空或不唯一
当前记录不唯一
来源 Hash 不一致
```

则该证据：

```text
identity_evidence_state = association_invalid
difference_valid = false
```

Claim Gate FAIL CLOSED 或逐记录阻断。

不得把 baseline/current-only 的 Association 行缺失或 unmatched 的空 `memory_id` 归入 Invalid；也不得把非 baseline query 的意外 Association 断链降级为 Current-only。

## 7.3 Schema

```text
evidence_id

current_inspection_id
current_frame_id
current_image_id
current_observation_id
current_timestamp
current_observation_source
current_comparability_status

previous_entity_type
previous_memory_id
previous_memory_version
previous_last_seen_inspection
previous_last_seen_timestamp
previous_source_inspection_ids
previous_source_record_count
previous_observation_sources
previous_comparability_status

comparison_group_id
metric_name
metric_type
value_domain
measurement_method
measurement_unit

current_value
previous_memory_snapshot_value
absolute_difference
relative_difference

association_id
association_status
association_mode
use_disease_id_score
association_score
match_type
candidate_count
score_margin
conflict_reason
needs_manual_review
association_supported_pair
identity_evidence_state

temporal_order_valid
difference_valid
relative_difference_valid
invalid_reason
comparison_comparability_status
comparability_reason

registration_status
registration_evidence_source
registration_evidence_sha256

physical_scale_calibrated
scale_calibration_source
scale_calibration_sha256

measurement_uncertainty
uncertainty_source
uncertainty_unit

source_current_record_fingerprint
source_association_artifact_sha256
source_association_manifest_sha256
source_memory_snapshot_sha256
source_engineering_artifact_sha256
```

### 7.3.1 Observation 与 Comparability 必填枚举

以下字段属于 Comparison Evidence 的 required 字段，不得为空、缺失或使用未知值：

```text
current_observation_source
current_comparability_status
previous_observation_sources
previous_comparability_status
comparison_comparability_status
```

Phase A `observation_source` 采用闭集枚举：

```text
kict_static_mask_cyclic_demo
real_inspection_mask_input
verified_fixture
mixed_sources
legacy_unverified_source
```

`previous_observation_sources` 必须存在且类型为去重、稳定排序的列表，每一项均来自同一枚举。只有 `previous_entity_type=not_applicable` 且 previous/comparison comparability 均为 `insufficient_history` 时允许空列表；存在历史 Memory Snapshot 时必须非空。新增来源必须升级 Claim/Evidence schema 版本并经过 Phase 0 review，禁止用任意非空字符串绕过校验。`verified_fixture` 仅允许 `execution_profile=test`，任何准备发布到正式 Manifest 的 Run 出现该来源都必须 Fail Closed。

`previous_entity_type=not_applicable` 时，所有 previous 字段使用唯一 canonical 表示：

|字段类型|Comparison Evidence CSV|ClaimDecision JSON|
|---|---|---|
|`previous_memory_id`、`previous_memory_version`、`previous_last_seen_inspection`、`previous_last_seen_timestamp`|空单元格；读取后规范化为 null|`null`|
|`previous_source_inspection_ids`、`previous_observation_sources`|JSON 数组文本 `[]`|`[]`|
|`previous_source_record_count`|整数 `0`|整数 `0`|
|`previous_memory_snapshot_value`、`absolute_difference`、`relative_difference`|空单元格；读取后规范化为 null|`null`|

禁止使用字符串 `"null"`、`"None"`、`"N/A"`、伪造 ID 或数值 `0` 代替 nullable scalar。Validator 必须在 CSV 解析后先完成 canonical normalization，再执行 Claim Policy。

Phase A `comparability_status` 采用闭集枚举：

```text
verified_comparable
not_longitudinally_comparable
insufficient_history
simulated_metadata_comparable
```

字段缺失、空值或不在枚举内：

```text
evidence_valid = false
identity_evidence_state = association_invalid
Claim Gate FAIL CLOSED
不得生成可发布 Static Audit
```

只有 schema 合法、且值明确属于后三种非 verified 状态时，才允许降级到 `static_descriptive_audit`。`mixed_sources` 和 `legacy_unverified_source` 是合法来源声明，但不会自动提升可比性。

## 7.4 历史数值字段

第一版至少支持明确可追溯的：

```text
inspection_level_max_mask_area_px:
  current_value                  ← 当前 inspection_id + 当前本地 observation 的 Engineering.max_area_px
  previous_memory_snapshot_value ← memory_before.last_area_px
  measurement_method            ← inspection_level_max_mask_area_px
  measurement_unit              ← pixel²
```

当前记录必须先通过 Association query 的中性复合键 `(inspection_id, frame_id, image_id, current_observation_id)` 在源 Frame/Observation records 中唯一定位，再通过 `current_observation_id`、来源记录指纹或 Engineering 中显式的 `source_observation_ids` 映射到唯一 Engineering 聚合行。Phase 0 若发现现有 Engineering schema 无法完成该映射，必须增加中性的来源引用字段并升级 schema；不得退回使用评估标签定位。

`label_disease_id` 只能在 Association 完成后用于 benchmark/evaluation 对照。Comparison Evidence、ClaimDecision、报告渲染和跨巡检身份判断均不得把它作为 required join key，也不得因其缺失而改变生产 Claim 结果。

`previous_memory_snapshot_value` 必须来自对应 round 的 `memory_before_query.csv`。两侧均为巡检级最大 mask 面积，不允许把单帧 `kict_area_px` 与巡检级 `last_area_px` 混合比较。

可比性合成规则按以下优先级固定，禁止使用单个 `else` 覆盖 `insufficient_history`：

```text
previous_entity_type == not_applicable
OR previous_comparability_status == insufficient_history
→ comparison_comparability_status = insufficient_history
→ difference_valid = false
→ 仅允许当前观测的 static_descriptive_audit

ELSE current_comparability_status == verified_comparable
AND previous_comparability_status == verified_comparable
→ comparison_comparability_status = verified_comparable

ELSE
→ comparison_comparability_status = not_longitudinally_comparable
→ difference_valid = false
→ 仅允许 static_descriptive_audit
```

其他 metric 必须逐项定义映射；未定义时：

```text
not_applicable
```

不得自动猜测。

## 7.5 禁止伪造 Observation

Phase A Schema 不包含：

```text
previous_observation_id
```

未来只有 Association 增加明确 provenance：

```text
selected_candidate_source_record_id
selected_candidate_source_inspection_id
selected_candidate_timestamp
selected_candidate_value
```

后，才可升级为 Observation-to-Observation。

---

# 8. Registration、Scale 与 Uncertainty Provenance

这些证据不得由 Association 推断。

## 8.1 字段

```text
registration_status
registration_evidence_source
registration_evidence_sha256
registration_schema_version

physical_scale_calibrated
scale_calibration_source
scale_calibration_sha256
scale_calibration_schema_version

measurement_uncertainty
uncertainty_source
uncertainty_unit
uncertainty_schema_version
```

## 8.2 Phase A 默认值

```text
registration_status = not_verified
registration_evidence_source = none
registration_evidence_sha256 = null

physical_scale_calibrated = false
scale_calibration_source = none
scale_calibration_sha256 = null

measurement_uncertainty = null
uncertainty_source = not_available
uncertainty_unit = null
```

## 8.3 禁止推断

以下事实不能自动产生配准或物理标定：

```text
里程接近
角度接近
Association matched
同一 memory_id
图像尺寸相同
单位为 pixel
```

## 8.4 可接受来源

只有以下受控来源可以放开：

```text
Prepared Dataset Manifest 中的明确证据块
项目级只读可信配置
人工复核签名 Artifact
经验证的配准/标定工具输出
```

所有来源必须具有：

```text
逻辑路径或 Artifact ID
SHA-256
Schema 版本
生成方式
生成时间
```

---

# 9. ClaimDecision v4

Run-local 机器证据的唯一权威路径：

```text
runs/<run_id>/artifacts/
├─ comparison_evidence.csv
├─ comparison_evidence_manifest.json
└─ claim_decision.json
```

`comparison_evidence` 和 `claim_decision` 由对应 DAG 节点各生成一次，先写临时文件并校验 Schema/Hash，再原子提升到上述 `artifacts/` 路径。后续 Growth Report、Memory Report、Visualization 和 FinalReport 只读取这些 Run-local 权威文件，不从 `outputs/` 或 Staging 反向取证。

兼容镜像只有成功发布后才允许出现在：

```text
outputs/claim_decision.json
```

该兼容镜像只能是权威 `runs/<run_id>/artifacts/claim_decision.json` 的逐字节副本，SHA-256 必须一致；它不是第二个生成位置，也不参与本 Run 的 Claim 决策。

Schema 示例：

```json
{
  "schema_version": "claim_decision_v4",
  "run_id": "run_012",
  "plan_fingerprint": "sha256...",
  "claim_policy_sha256": "sha256...",
  "claim_gate_version": "1.0.0",
  "source_comparison_evidence_sha256": "sha256...",
  "source_association_artifact_sha256": "sha256...",
  "source_association_manifest_sha256": "sha256...",
  "record_decisions": [
    {
      "decision_id": "CD-I002-ASSOC-I002-1-img-2-1-area",
      "evidence_id": "EVD-I002-ASSOC-I002-1-img-2-1-area",
      "current_record_id": "ASSOC-I002-1-img-2-1",
      "previous_entity_type": "memory_snapshot",
      "previous_memory_id": "MEM-D001",
      "comparison_group_id": "MEM-D001::inspection_level_max_mask_area_px",
      "identity_evidence_state": "association_supported",
      "current_observation_source": "real_inspection_mask_input",
      "current_comparability_status": "not_longitudinally_comparable",
      "previous_observation_sources": ["real_inspection_mask_input"],
      "previous_comparability_status": "not_longitudinally_comparable",
      "comparison_comparability_status": "not_longitudinally_comparable",
      "source_record_fingerprint": "sha256...",
      "source_memory_snapshot_sha256": "sha256...",
      "capabilities": {
        "static_descriptive_audit": "allowed",
        "descriptive_difference_claim": "blocked",
        "directional_change_claim": "blocked",
        "physical_quantity_change_claim": "blocked",
        "multi_timepoint_pattern_claim": "blocked",
        "prediction_claim": "blocked"
      },
      "required_language_qualifiers": [
        "基于当前规则关联候选",
        "不构成真实身份确认",
        "当前证据不可纵向比较，仅允许静态描述审计，不构成方向性变化结论"
      ],
      "reason_codes": [
        "RULE_ASSOCIATION_ONLY",
        "NOT_LONGITUDINALLY_COMPARABLE",
        "REGISTRATION_NOT_VERIFIED",
        "PHASE_A_NO_PHYSICAL_QUANTITY_EVIDENCE",
        "PHASE_A_NO_THREE_TIMEPOINT_EVIDENCE",
        "PHASE_A_NO_VALIDATED_PREDICTION_MODEL"
      ]
    }
  ],
  "summary": {
    "total_records": 1,
    "static_audit_allowed": 1,
    "difference_allowed_with_limits": 0,
    "difference_blocked": 1,
    "directional_allowed": 0,
    "physical_allowed": 0,
    "pattern_allowed": 0,
    "prediction_allowed": 0
  }
}
```

任一来源 Hash 变化：

```text
旧 ClaimDecision 作废
不得复用
```

---

# 10. 所有人类可读产物的 Claim Gate 发布合同

## 10.1 发布层原则

```text
内部算法 Artifact
≠
正式工程结论
```

所有正式发布的人类可读报告必须：

```text
实际读取 claim_decision.json
验证 ClaimDecision 来源 Hash
根据 capability 状态选择模板
验证限定性用语
禁止读取旧 growth_trend 后直接发布
```

仅在 DAG 上增加 `deps` 不算完成。

## 10.2 Growth Report

拟新增：

```text
orchestrator/agents/growth_report_agent.py
```

输入：

```text
comparison_evidence
claim_decision
```

输出到 Staging：

```text
disease_growth_analysis_report.md
disease_growth_analysis_summary.md
```

禁止直接发布旧字段：

```text
growth_trend
growth_description
area_growth_rate
```

可以把原始数值放在“内部证据附录”，但必须附 Claim 状态。

## 10.3 Memory Data 与 Memory Report 分离

`memory` 节点继续生成 CSV。

拟新增：

```text
memory_report
orchestrator/agents/memory_report_agent.py
```

输入：

```text
disease_memory_bank.csv
claim_decision.json
```

输出到 Staging：

```text
memory_agent_report.md
disease_memory_bank_summary.md
```

现有 `MemoryAgent._write_reports()` 在 Phase A 改为：

```text
不再写正式 outputs
只写 internal_neutral 报告，或由兼容参数完全关闭
```

history-only round 的 Memory 报告必须明确：

```text
内部候选 Memory Snapshot
非正式工程结论
```

并删除：

```text
呈……趋势
持续增长
恶化
改善
```

## 10.4 Visualization 与 Recheck

更新真实 `VisualizationAgent` 输入合同，必须增加：

```text
comparison_evidence
claim_decision
```

配置示意：

```yaml
visualization:
  comparison_evidence: runs/<run_id>/artifacts/comparison_evidence.csv
  claim_decision: runs/<run_id>/artifacts/claim_decision.json
  ...
```

所有：

```text
图标题
图例
图中注释
复检优先级理由
Visualization Report
Visualization Summary
Recheck Report
```

必须按 Claim 状态渲染。

规则身份为 `allowed_with_limits` 时，图表必须显式标注：

```text
rule-associated comparison
not identity-verified
```

## 10.5 FinalReport

更新 `FinalReportAgent` 输入合同：

```text
comparison_evidence
claim_decision
growth_report
memory_report
```

禁止直接从旧 Growth CSV 的：

```text
growth_trend
growth_description
```

生成正式结论。

## 10.6 机器校验

Publication Validation 的主校验必须基于结构化映射：

```text
report_record_id
decision_id
template_id
capability
claim_status
required_language_qualifiers
```

Renderer 只能从 Claim Policy 注册的受控模板生成正式结论；Validator 必须逐记录验证 `template_id` 与 ClaimDecision capability/status 一致，并确认必要限定语存在。

当 `comparison_comparability_status != verified_comparable` 且 schema 合法时，ClaimDecision 必须加入受控限定语模板：

```text
当前证据不可纵向比较，仅允许静态描述审计，不构成方向性变化结论
```

缺少该限定语时 Publication Validation 必须失败，不能只依赖 `reason_codes` 或受限词扫描推断边界。

受限词扫描只作为补充防线，扫描以下高风险词：

```text
同一病害
确认增长
持续扩大
长期趋势
持续恶化
未来会
```

若 ClaimDecision 不允许却出现相关词：

```text
VALIDATION_FAILED
不得发布
```

但不得仅因否定性边界说明包含字面词语就失败，例如“不能确认是同一病害”。允许的免责声明必须来自受控 disclaimer template，并通过 `template_id` 验证，禁止实现自由文本情感或否定词推断。

---

# 11. Staging 与 Publication Transaction

## 11.1 必要性

旧成功 Run 的正式文件可能仍位于：

```text
outputs/
```

新 Run 失败时，不能仅依靠“未覆盖文件”判断当前结果。

当前正式发布必须由：

```text
outputs/current_publication_manifest.json
```

唯一标识。

## 11.2 Staging

每 Run：

```text
runs/<run_id>/artifacts/
├─ comparison_evidence.csv
├─ comparison_evidence_manifest.json
└─ claim_decision.json

runs/<run_id>/staging/
├─ disease_engineering_report.md
├─ disease_engineering_report_summary.md
├─ disease_growth_analysis_report.md
├─ disease_growth_analysis_summary.md
├─ memory_agent_report.md
├─ disease_memory_bank_summary.md
├─ priority_recheck_list.csv
├─ visualizations/
├─ visualization_report.md
├─ visualization_summary.md
├─ recheck_list_report.md
├─ final_project_report.md
├─ system_summary.md
├─ key_insights.md
├─ claim_decision.json             # 权威 Run-local 文件的发布镜像
└─ final_summary.md
```

`artifacts/` 保存本 Run 的机器证据，供 DAG 内部消费；`staging/` 只保存待发布的人类可读产物和明确标注的兼容镜像。`comparison_evidence.csv` 不复制到 `data/simulated/`，避免形成第二个权威来源。

## 11.3 发布前条件

```text
核心 DAG 成功
Comparison Evidence Schema 通过
ClaimDecision Schema 通过
报告来源 Hash 一致
Staging claim_decision 镜像与 Run-local 权威文件 Hash 一致
受限词校验通过
Staging Artifact Validation 通过
Staging final_summary.md 生成并通过验证
```

## 11.4 Manifest-last 事务

多文件系统不能保证多个 `os.replace()` 同时原子，因此采用：

```text
备份 + 逐文件原子替换 + Manifest 最后提交 + 可恢复回滚
```

步骤：

```text
1. 确认 Run-local comparison_evidence 与 claim_decision 已经原子落盘并通过 Hash/Schema 校验；
2. 创建 runs/<run_id>/publication_backup/ 和 transaction_state.json；
3. 为每个目标记录 destination_path、staging_path、existed_before、backup_path、new_sha256 和 parent_directory；只对 existed_before=true 的文件创建备份；
4. 将除 `final_summary.md` 外的每个待发布 Staging Artifact 原子替换到正式路径；`current_publication_manifest.json` 不属于 Staging Artifact；
5. 每次替换后校验 SHA-256；
6. 将 staging/final_summary.md 原子提升为 runs/<run_id>/final_summary.md，并校验 Hash；
7. 生成 current_publication_manifest.json.tmp，记录正式文件、Run-local 证据和 final_summary 的 Hash；
8. fsync 临时 Manifest；
9. os.replace(tmp, current_publication_manifest.json)，这是唯一可见提交点；
10. 对本事务发生 replace/delete/restore 的每个唯一目标父目录分别 fsync，包括但不限于 outputs、outputs/visualizations、data/simulated、runs/<run_id> 和 Manifest 所在目录；
11. StateStore.transition_status(RUNNING → COMPLETED)；
12. 释放 Active Run Lock；
13. 清理 backup。
```

Manifest 是可见提交点。Phase A 新增的 workflow/CLI consumers 必须：

```text
先读 Manifest
再验证文件 Hash
```

不得只按文件存在判断。

Phase A 不修改现有 Web。现有 Web 在 Phase E 接入 Manifest 前仍是 legacy demo reader，不能被描述为“当前可信发布结果”的权威读取者。

## 11.5 Publication Manifest

```json
{
  "schema_version": "publication_manifest_v1",
  "run_id": "run_012",
  "plan_fingerprint": "sha256...",
  "published_at": "...",
  "source_artifacts": {
    "engineering_report": {
      "path": "runs/run_012/work/disease_engineering_report.csv",
      "sha256": "..."
    },
    "growth_results": {
      "path": "runs/run_012/work/disease_growth_results.csv",
      "sha256": "..."
    },
    "memory_bank": {
      "path": "runs/run_012/work/disease_memory_bank.csv",
      "sha256": "..."
    },
    "association_records": {
      "path": "runs/run_012/work/disease_association_records.csv",
      "sha256": "..."
    },
    "association_manifest": {
      "path": "runs/run_012/work/main_progressive/association_manifest.json",
      "sha256": "..."
    },
    "association_round_I002_query_frames": {
      "path": "runs/run_012/work/main_progressive/round_I002/query_frames.csv",
      "sha256": "..."
    },
    "association_round_I002_memory_before": {
      "path": "runs/run_012/work/main_progressive/round_I002/memory_before_query.csv",
      "sha256": "..."
    },
    "association_round_I002_records": {
      "path": "runs/run_012/work/main_progressive/round_I002/association_records.csv",
      "sha256": "..."
    },
    "association_round_I002_memory_after": {
      "path": "runs/run_012/work/main_progressive/round_I002/memory_after_query.csv",
      "sha256": "..."
    },
    "comparison_evidence": {
      "path": "runs/run_012/artifacts/comparison_evidence.csv",
      "sha256": "..."
    },
    "comparison_evidence_manifest": {
      "path": "runs/run_012/artifacts/comparison_evidence_manifest.json",
      "sha256": "..."
    },
    "claim_decision": {
      "path": "runs/run_012/artifacts/claim_decision.json",
      "sha256": "..."
    },
    "final_summary": {
      "path": "runs/run_012/final_summary.md",
      "sha256": "..."
    }
  },
  "artifacts": {
    "disease_engineering_report": {
      "path": "outputs/disease_engineering_report.md",
      "sha256": "..."
    },
    "disease_engineering_report_summary": {
      "path": "outputs/disease_engineering_report_summary.md",
      "sha256": "..."
    },
    "disease_growth_analysis_report": {
      "path": "outputs/disease_growth_analysis_report.md",
      "sha256": "..."
    },
    "disease_growth_analysis_summary": {
      "path": "outputs/disease_growth_analysis_summary.md",
      "sha256": "..."
    },
    "memory_agent_report": {
      "path": "outputs/memory_agent_report.md",
      "sha256": "..."
    },
    "disease_memory_bank_summary": {
      "path": "outputs/disease_memory_bank_summary.md",
      "sha256": "..."
    },
    "priority_recheck_list": {
      "path": "data/simulated/priority_recheck_list.csv",
      "sha256": "..."
    },
    "visualization_report": {
      "path": "outputs/visualization_report.md",
      "sha256": "..."
    },
    "visualization_summary": {
      "path": "outputs/visualization_summary.md",
      "sha256": "..."
    },
    "recheck_list_report": {
      "path": "outputs/recheck_list_report.md",
      "sha256": "..."
    },
    "final_project_report": {
      "path": "outputs/final_project_report.md",
      "sha256": "..."
    },
    "system_summary": {
      "path": "outputs/system_summary.md",
      "sha256": "..."
    },
    "key_insights": {
      "path": "outputs/key_insights.md",
      "sha256": "..."
    },
    "claim_decision_mirror": {
      "path": "outputs/claim_decision.json",
      "sha256": "..."
    },
    "visualizations/area_audit.png": {
      "path": "outputs/visualizations/area_audit.png",
      "sha256": "..."
    }
  }
}
```

以上只展示单个可视化文件的结构；真实 Manifest 必须穷举本次 Staging 中全部待发布文件，禁止只登记代表性样例。`visualizations/` 必须递归展开为以 POSIX 相对路径稳定排序的逐文件条目，每个文件独立记录 SHA-256；不得只记录目录路径、mtime 或目录总大小。

发布前必须计算并比较：

```text
expected_publication_paths = Staging 中除 final_summary.md 外的全部待发布文件映射到正式路径后的集合
manifest_artifact_paths = Manifest artifacts 中全部 path 的集合
expected_source_artifact_paths = 本 Run work/artifacts 目录、其 manifest 递归引用的全部必要机器 Artifact，以及 runs/<run_id>/final_summary.md 的集合
manifest_source_artifact_paths = Manifest source_artifacts 中全部 path 的集合

expected_publication_paths == manifest_artifact_paths
expected_source_artifact_paths == manifest_source_artifact_paths
```

同时验证无重复路径、无未登记文件、无 Manifest 指向但 Staging/Run-local 来源不存在的文件。Association Manifest 引用的 round/query/memory/association 文件必须展开到 `source_artifacts`，或以显式的递归 child-artifacts 列表逐文件记录路径与 Hash；只登记顶层 Manifest 而不验证其引用文件不算完整。`claim_decision_mirror` 的 Hash 还必须等于 Run-local 权威 `claim_decision.json`。任一集合或 Hash 不一致均不得提交 Manifest。

## 11.6 发布失败

```text
Manifest 尚未提交
→ 按 transaction_state 逆序回滚每个目标
→ existed_before=true：从 backup 恢复并复核旧 Hash
→ existed_before=false：删除本次新建的正式文件并 fsync 其父目录
→ 最后恢复旧 Manifest；首次发布没有旧 Manifest 时确认提交位置不存在 Manifest

恢复成功
→ 保留旧 Manifest
→ 当前 Run FAILED

恢复不完整
→ 移除或隔离 current_publication_manifest.json
→ 写 outputs/PUBLICATION_RECOVERY_REQUIRED.json
→ 当前结果不可用
→ FAIL CLOSED
```

Publication Transaction 必须在 `transaction_state.json` 记录以下阶段：

```text
backup_ready
files_replaced
final_summary_ready
manifest_committed
state_completed
```

`transaction_state.json` 至少包含：

```json
{
  "transaction_id": "txn-uuid",
  "run_id": "run_012",
  "phase": "backup_ready",
  "old_manifest_existed": true,
  "old_manifest_backup_path": "runs/run_012/publication_backup/current_publication_manifest.json",
  "targets": [
    {
      "destination_path": "outputs/final_project_report.md",
      "staging_path": "runs/run_012/staging/final_project_report.md",
      "existed_before": true,
      "backup_path": "runs/run_012/publication_backup/outputs/final_project_report.md",
      "old_sha256": "...",
      "new_sha256": "...",
      "parent_directory": "outputs"
    }
  ]
}
```

`existed_before=false` 时 `backup_path` 和 `old_sha256` 必须为 null。回滚完成后，正式文件路径集合与旧 Manifest 必须完全一致；首次发布回滚后，正式位置不得残留本事务新增文件或 current Manifest。

Controller 启动或恢复时若发现未清理的 publication backup：

```text
manifest_committed 之前崩溃
→ 先隔离任何临时 Manifest
→ 按 backup 恢复旧正式文件与旧 Manifest
→ 恢复完整后标记当前 Run FAILED

manifest_committed 之后、COMPLETED 之前崩溃
→ 验证新 Manifest、全部正式文件、Run-local 证据和 final_summary Hash
→ state 仍为预期 RUNNING 且 plan_fingerprint/run_id 一致时，幂等补做 COMPLETED
→ 任一校验失败或 state 冲突时，先隔离新 Manifest，再将 runs/<run_id>/final_summary.md 原子移动到 staging/invalidated_final_summary.<transaction_id>.md 并写 invalidation reason，然后按 transaction_state 逆序恢复旧发布
→ final_summary 隔离、旧发布恢复或旧 Manifest 恢复任一步骤不完整时，写 recovery marker 并 FAIL CLOSED
```

`invalidated_final_summary.<transaction_id>.md` 必须使用本事务不可变 ID。目标已存在表示事务审计状态冲突，必须 Fail Closed；禁止覆盖、复用固定文件名或静默追加。

不得在 Manifest 已提交后继续套用“Manifest 不更新”的失败描述；该窗口必须按上述提交后恢复规则处理。

失败 Run：

```text
不得保留可被识别为成功结果的 runs/<run_id>/final_summary.md
无论 Manifest 提交前或提交后，只要当前 Run 最终不是 COMPLETED，已生成的 final_summary 都必须移动到 Staging 审计区的 transaction-scoped 文件并标记 invalidated
生成 runs/<run_id>/failure_summary.md
保留 Staging 供审计
```

---

# 12. Prepared Dataset 与 Legacy Simulated 双入口

## 12.1 新入口

```bash
python scripts/run_inspection_workflow.py --task-file ...
```

只接受：

```text
prepared_dataset
```

TaskRequest：

```json
{
  "schema_version": "inspection_task_v1",
  "task_id": "task_001",
  "task_type": "inspection_analysis",
  "input": {
    "input_mode": "prepared_dataset",
    "dataset_id": "pilot_001"
  },
  "requested_outputs": [
    "association",
    "growth_report",
    "visualization",
    "final_report"
  ]
}
```

必须调用：

```python
require_inference_ready(
    prepared_dataset_dir / "preparation_manifest.json"
)
```

## 12.2 旧入口唯一方案

```bash
python run.py --mode full_pipeline
```

Phase A 明确作为：

```text
legacy_simulated
```

它不伪装成 Prepared Dataset TaskRequest。

输入继续使用：

```text
data/simulated/robot_kict_frame_records.csv
```

但必须共享：

```text
同一个 ActiveRunLock
同一个 config/dag.yaml
同一个 DAGExecutor
同一个 StateStore
同一个 Claim Gate
同一个 Publication Transaction
```

## 12.3 公共内部入口

拟新增 Controller 提供两个明确入口：

```python
InspectionWorkflowController.run_prepared_task(task_request)
InspectionWorkflowController.run_legacy_simulated()
```

二者在输入解析后汇合为内部：

```text
ResolvedWorkflowInput
```

然后共享 Planner、锁、Run、DAGExecutor、Claim、发布流程。

## 12.4 后续统一

只有未来创建：

```text
data/prepared_inspections/simulated_demo/
├─ observation_records.csv
├─ frame_records.csv
└─ preparation_manifest.json
```

后，legacy 入口才可迁移为兼容 TaskRequest。

不属于当前 Phase A。

---

# 13. Planner 与 Fingerprint

## 13.1 Planner

必须使用真实：

```python
build_dag(config/dag.yaml)
```

从请求输出节点反向计算依赖闭包。

`config/inspection_workflow.yaml` 只保存：

```text
output 映射
validation policy
path policy
lock policy
publication policy
```

禁止复制 DAG 拓扑和重试配置。

## 13.2 Prepared Dataset Fingerprint

必须包含：

```text
规范化选中 DAG 子图 Hash
workflow policy Hash
output mapping Hash
claim policy Hash
TaskRequest Schema version
preparation_manifest Hash
已验证 observation_records Hash
已验证 frame_records Hash
source_metadata Hash
source_artifacts 规范化集合 Hash
requested_outputs
task_type
input_mode
```

## 13.3 Legacy Simulated Fingerprint

必须包含：

```text
规范化选中 DAG 子图 Hash
workflow policy Hash
claim policy Hash
data/simulated/robot_kict_frame_records.csv 实际 Hash
config/dag.yaml 选中输入配置 Hash
input_mode=legacy_simulated
```

## 13.4 Plan-only

```text
只支持 prepared TaskRequest
执行 Readiness 和 Fingerprint
不获取 Active Run Lock
不创建 Run
不写 StateStore
不写业务 Artifact
不发布
```

---

# 14. Active Run Lock 与 run_NNN

## 14.1 保留 run_NNN

Phase A 保留现有：

```text
run_NNN
```

全局 Active Run Lock 已经串行化 Run 分配，不需要迁移 ULID。

## 14.2 锁路径

```text
runs/.active_run.lock
```

原子创建：

```python
os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
```

## 14.3 Allocation 阶段

首次锁内容：

```json
{
  "schema_version": "active_run_lock_v2",
  "phase": "allocating",
  "allocation_token": "uuid",
  "reserved_run_id": null,
  "run_id": null,
  "task_id": "task_001",
  "pid": 12345,
  "hostname": "host-a",
  "lock_token": "uuid",
  "created_at": "..."
}
```

在持锁情况下：

```text
在锁内计算唯一 reserved_run_id=run_NNN
→ 原子更新锁：
   phase=allocating
   allocation_token=<uuid>
   reserved_run_id=run_NNN
→ 此时尚未创建 Run 目录
→ RunManager.create_run(
    run_id=reserved_run_id,
    dag_config=...,
    initial_metadata={
        "allocation_token": allocation_token,
        "task_id": task_id,
        "lock_token": lock_token,
        "reserved_run_id": reserved_run_id,
    },
)
→ metadata.json 首次原子写入成功后，Run 才算 created
→ 原子更新锁：
   phase=running
   run_id=reserved_run_id
→ DAGExecutor(..., run_id=reserved_run_id)
```

`RunManager.create_run()` 必须接受锁内已预留的 `run_id`，不得再次调用 `_next_run_number()`。目录创建和首次 metadata 写入之间发生硬崩溃时，锁内 `reserved_run_id` 是恢复该无 metadata 目录的唯一权威引用。

禁止以下旧顺序：

```text
RunManager.create_run(
    dag_config=...,
    initial_metadata={
        "allocation_token": allocation_token,
        "task_id": task_id,
        "lock_token": lock_token,
    },
)
→ 得到 run_NNN
→ 原子更新锁：
   phase=running
   run_id=run_NNN
→ DAGExecutor(..., run_id=run_NNN)
```

`allocation_token` 必须在 `metadata.json` 的首次写入中出现，禁止先写无 token metadata 再 patch。若首次 metadata 写入失败，`create_run()` 必须清理未发布的空 Run 目录；清理失败时在 `reserved_run_id` 目录内保留显式 `.allocation_recovery_required` marker 并 FAIL CLOSED。

## 14.4 Allocation 崩溃恢复

当锁为 `allocating` 且同主机 PID 不存在：

```text
先读取锁中的 reserved_run_id 和 allocation_token

reserved_run_id 为 null
→ 该状态只允许出现在首次锁创建后、Run ID 预留前
→ 此时按合同尚不允许创建任何 Run 目录
→ 确认没有与该 allocation_token 关联的 metadata/recovery marker 后，可审计删除过期锁
→ 若发现无法归属的新增 Run 目录或锁字段非法，FAIL CLOSED

reserved_run_id 非 null
→ 只检查 runs/<reserved_run_id>/，禁止扫描后猜测其他 Run

reserved_run_id 目录不存在
→ 可审计删除锁

reserved_run_id 目录存在但 metadata.json 不存在
→ 目录为空时仅可在审计记录后删除该目录和锁
→ 目录非空或清理失败时写 .allocation_recovery_required 并 FAIL CLOSED

metadata.json 存在且 allocation_token/run_id 与锁一致，状态为 created
→ 将该 Run 标记 FAILED / allocation_aborted
→ 删除锁

metadata token/run_id 不一致、目录名称不一致或状态不一致
→ FAIL CLOSED
```

## 14.5 文件系统边界

Phase A 仅支持：

```text
单机本地 NTFS
单机本地 Linux 常规文件系统
```

不声称支持多主机 NFS/SMB。

不实现持续 Heartbeat。

---

# 15. StateStore、Checkpoint、CAS 与 WAL

## 15.1 Canonical State

唯一状态真相：

```text
runs/<run_id>/state.json
```

```text
orchestrator/state/run_state.json
```

仅为兼容镜像，不参与 Resume、Web 决策或状态判断。

`metadata.json.status` 只是 state 投影。

## 15.2 StateStore API

在真实文件中演进：

```text
orchestrator/state/store.py
```

拟新增：

```python
class StateStore:
    def initialize_run(self, *, run_id, initial_context, initial_status):
        ...

    def load(self, *, run_id):
        ...

    def checkpoint_context(
        self,
        *,
        run_id,
        expected_status,
        expected_state_version,
        event_id,
        task_status,
        completed_tasks,
        failed_tasks,
        context_snapshot,
    ):
        ...

    def transition_status(
        self,
        *,
        run_id,
        expected_status,
        expected_state_version,
        transition_id,
        next_status,
        metadata=None,
    ):
        ...

    def recover(self, *, run_id):
        ...
```

## 15.3 两类写入

### Context Checkpoint

用于：

```text
任务开始
任务成功
任务失败
Retry event
task_status
completed_tasks
failed_tasks
context_snapshot
```

调用：

```text
checkpoint_context()
```

### 顶层状态迁移

用于：

```text
CREATED → PLANNED
PLANNED → RUNNING
RUNNING → FAILED
RUNNING → COMPLETED
RUNNING → WAITING_FOR_REVIEW
```

调用：

```text
transition_status()
```

每个任务 checkpoint 不得冒充顶层状态迁移。

## 15.4 DAGExecutor 适配

Phase A 必须修改：

```text
DAGExecutor._checkpoint()
DAGExecutor.run_async()
DAGExecutor._run_task()
```

使其：

```text
通过 StateStore.checkpoint_context() 写任务上下文
不再直接写全局和 per-run 两套 JSON 真相
不再直接用 RunManager.update_metadata() 决定权威状态
```

Controller 在运行开始、失败、发布成功时调用：

```text
transition_status()
```

旧：

```text
save_checkpoint
load_checkpoint
make_state
```

只保留兼容读取/包装，不得继续形成独立写路径。

## 15.5 State Lock

```text
runs/<run_id>/.state.lock
```

内容：

```json
{
  "schema_version": "state_lock_v1",
  "run_id": "run_012",
  "pid": 12345,
  "hostname": "host-a",
  "lock_token": "uuid",
  "created_at": "..."
}
```

自动清理仅允许：

```text
同主机
AND PID 不存在
```

跨主机 FAIL CLOSED。

## 15.6 CAS

每次写入都验证：

```text
expected_state_version
```

`transition_status()` 还验证：

```text
expected_status
transition_id
```

同一旧版本下仅一个不同写入成功。

## 15.7 Recovery 前置条件

Controller 对已有 Run 执行 resume、retry、publication recovery 或状态查询后继续写入前，必须：

```text
获取 runs/<run_id>/.state.lock
→ StateStore.recover(run_id)
→ 确认不存在 unresolved pending / journal conflict
→ 才允许 load 后继续 checkpoint_context 或 transition_status
```

为防止调用者遗漏，`checkpoint_context()` 和 `transition_status()` 在持有 state lock 后也必须先调用内部 `_recover_unfinished_operations_locked()`。存在无法恢复的 pending、中间损坏、checksum 冲突或 payload 冲突时，mutation API 必须直接返回 `STATE_CONFLICT`/FAIL CLOSED，不得在其后追加新的 pending。

公开 `recover()` 与 mutation API 的内部恢复必须复用同一实现；禁止形成两套恢复算法或嵌套重复获取同一文件锁。新 Run 的 `initialize_run()` 必须确认 Journal 不存在或为空。

---

# 16. WAL 与崩溃恢复

## 16.1 Unified State Journal

Phase A 只允许一份 State WAL：

```text
runs/<run_id>/state_journal.jsonl
```

`checkpoint_context()` 与 `transition_status()` 都必须写入该 Journal，不得只有顶层状态迁移受 WAL 保护。记录阶段：

```text
pending
committed
aborted
```

每行公共字段：

```text
schema_version
operation_kind        # context_checkpoint | status_transition
operation_id          # checkpoint 使用 event_id；transition 使用 transition_id
phase                 # pending | committed | aborted
expected_state_version
resulting_state_version
expected_status
resulting_status
timestamp
payload_sha256
record_checksum
```

`status_transition` 的 `resulting_status` 是 `next_status`；`context_checkpoint` 的 `resulting_status` 必须等于 `expected_status`，只能更新 task/context 字段和 `state_version`，不得伪造顶层状态迁移。

Canonical `state.json` 必须额外保存：

```text
last_operation_kind
last_operation_id
last_operation_payload_sha256
```

这三个字段与 `state_version` 一起用于判断 pending 是否已经应用。Checksum 对除 `record_checksum` 外的规范化 JSON 计算。

## 16.2 两类写入的统一顺序

```text
获取 state lock
→ 读取 state 与 state_journal
→ 相同 operation_kind + operation_id 已 committed：验证 payload_sha256 后返回原结果
→ 相同 ID 但 payload 不同：STATE_CONFLICT
→ 验证 expected status/version
→ 追加 pending，flush+fsync
→ 原子替换 state.json；同时写 resulting version 和 last_operation_* 三字段
→ 追加 committed，flush+fsync
→ 平台允许时 fsync 目录
→ 释放 state lock
```

因此 `checkpoint_context()` 的任务开始、成功、失败和 Retry checkpoint 与 `transition_status()` 使用同一套 CAS/WAL 顺序；不得建立第二份 checkpoint journal。

## 16.3 JSONL 损坏

```text
最后一行不完整
→ 忽略该残行
→ 记录 recovery warning
→ 按最后一个完整 pending/state 恢复

中间任意行无法解析
→ FAIL CLOSED

record_checksum 不匹配
→ FAIL CLOSED

同一 operation_kind + operation_id 有冲突 committed
→ STATE_CONFLICT
```

## 16.4 恢复规则

```text
pending + state 的 version/last_operation_id/payload_sha256 均表明操作已应用
→ 补写 committed

pending + state 仍为 expected version/status
→ 追加 aborted
→ 允许使用新 operation_id 重试；使用相同 ID 时 payload 必须完全一致

pending + state 为其他版本或状态
→ STATE_CONFLICT

committed 已存在
→ 同一 operation_kind + operation_id 且 payload 相同：返回原结果
→ payload 不同：STATE_CONFLICT
→ 不增加 state_version
```

`checkpoint_context()` 恢复后必须验证 `task_status`、`completed_tasks`、`failed_tasks` 和 `context_snapshot` 的规范化 Hash 与 pending payload 一致；只比较 `state_version` 不足以证明 checkpoint 已应用。

---

# 17. Run 状态兼容映射

Canonical 状态：

```text
CREATED
PLANNED
RUNNING
WAITING_FOR_REVIEW
BLOCKED
FAILED
COMPLETED
```

旧 metadata/Web 投影：

```text
CREATED            → created
PLANNED            → planned
RUNNING            → running
WAITING_FOR_REVIEW → waiting_for_review
BLOCKED            → blocked
FAILED             → failed
COMPLETED          → success
```

新代码禁止写：

```text
completed
complete
partial_success
partial
```

历史 Run 中的 `partial` 读取规则：

```text
存在 failed task → FAILED
存在 running/pending task → RUNNING
否则 → BLOCKED，并标记 legacy_status_inferred=true
```

Phase A 的 `RunManager.list_runs()` 与 workflow/CLI consumers 优先读取：

```text
state.json.status
```

metadata status 只用于旧 Run 兼容。

现有 Web 本轮不修改，继续作为 legacy demo reader。Web 对 canonical state 和 Publication Manifest 的接入属于 Phase E，不得在 Phase A 验收中声称已经完成。

---

# 18. Path Safety

Prepared TaskRequest 只接受：

```text
dataset_id
```

规则：

```text
仅 [A-Za-z0-9._-]
不能为空
不能是 . 或 ..
不能包含路径分隔符
不能包含 ::
```

配置：

```yaml
path_policy:
  prepared_dataset_base: data/prepared_inspections
  allow_symlinks: false
```

检查：

```text
base.resolve(strict=True)
(base / dataset_id).resolve(strict=True)
确认 candidate 仍在 base 内
逐层 lstat
Windows 检查 Junction/Reparse Point
```

独立：

```text
prepare_real_inspection_pilot.py
```

仍可由管理员传入受控绝对源路径；TaskRequest 不能临时开放外部根目录。

---

# 19. Artifact Isolation

复用并扩展：

```text
tests/conftest.py::PROTECTED_ARTIFACTS
artifact_snapshot()
artifact_manifest_diff()
```

新增保护：

```text
outputs/current_publication_manifest.json
outputs/PUBLICATION_RECOVERY_REQUIRED.json
outputs/claim_decision.json
runs/*/artifacts/comparison_evidence.csv
runs/*/artifacts/comparison_evidence_manifest.json
runs/*/artifacts/claim_decision.json
runs/*/staging/
runs/*/publication_backup/
```

所有 Workflow 测试：

```text
使用 tmp_path 项目根
真实仓库 added=[]
真实仓库 removed=[]
真实仓库 modified=[]
```

临时项目中必须验证：

```text
成功 Run：Manifest 与正式文件 Hash 一致
失败 Run：Manifest 不更新
Claim Gate 失败：Staging 不发布
Final Summary 失败：不进入 COMPLETED
锁冲突：不创建业务产物
回滚失败：Manifest 被隔离并产生恢复标记
```

---

# 20. Semantic Snapshot 与更新规则

目录：

```text
tests/fixtures/semantic_snapshots/
```

比较：

```text
Association 核心字段
Memory 核心聚合字段
旧 Growth 数值字段
```

排除：

```text
时间戳
run_id
绝对路径
临时目录
非语义排序
Markdown 格式
```

浮点使用明确容差。

## 20.1 禁止自动更新

测试不得自动更新 Snapshot。

拟新增：

```text
scripts/update_semantic_snapshots.py
```

默认：

```text
只计算 diff
不写文件
```

只有：

```bash
python scripts/update_semantic_snapshots.py --write --reason "<reviewed reason>"
```

才能更新。

更新必须生成：

```text
tests/fixtures/semantic_snapshots/change_report.md
```

记录：

```text
旧 Hash
新 Hash
变更字段
原因
Association 阈值是否变化
Memory 语义是否变化
Growth 公式是否变化
```

本 Phase 禁止修改相关算法，因此原则上不得更新 Snapshot。

---

# 21. CLI 退出码与真实 Sandbox 测试

退出码：

```text
0 = COMPLETED / PLAN_ONLY_SUCCESS
2 = INVALID_TASK_REQUEST
3 = BLOCKED_BY_READINESS
4 = ACTIVE_RUN_CONFLICT
5 = EXECUTION_FAILED
6 = VALIDATION_FAILED
7 = WAITING_FOR_REVIEW
8 = LOCK_ERROR
9 = STATE_CONFLICT
```

CLI 测试不能只调用 Python 函数。

必须：

```text
1. 将最小项目复制到 tmp_path；
2. cwd=临时项目根；
3. subprocess 执行真实 CLI；
4. 设置 FAST_TEST_MODE 等必要环境；
5. 验证退出码和临时 Artifact；
6. 验证真实仓库无变化。
```

真实命令：

```bash
python run.py --mode full_pipeline
```

以及：

```bash
python scripts/run_inspection_workflow.py --task-file ...
```

必须测试双向锁冲突。

---

# 22. Final Summary、Publication 与 COMPLETED 顺序

固定成功顺序：

```text
1. DAG 核心节点成功；
2. Evidence 与 ClaimDecision Schema 通过；
3. 在 Staging 生成全部正式报告；
4. Staging Validation 通过；
5. 在 Staging 生成 final_summary.md；
6. 将 Staging final_summary 原子提升为 runs/<run_id>/final_summary.md 并校验；
7. Publication Transaction 完成全部正式文件替换；
8. 最后提交 current_publication_manifest.json；
9. StateStore.transition_status(RUNNING → COMPLETED)；
10. 释放 Active Run Lock。
```

Manifest 提交前的任何失败：

```text
不进入 COMPLETED
不生成成功式 final_summary
不更新 current_publication_manifest
生成 failure_summary
状态进入 FAILED
```

Manifest 提交后、COMPLETED 前的失败按第 11.6 节恢复，不得声称 Manifest 从未更新。

---

# 23. Phase 0 交付物

```text
docs/inspection_agent_contract.md
docs/inspection_claim_policy.md
docs/inspection_comparison_evidence_contract.md
docs/inspection_publication_contract.md
docs/inspection_state_store_contract.md
config/inspection_workflow.yaml
tests/fixtures/inspection_workflow/task_valid.json
tests/fixtures/inspection_workflow/task_invalid.json
```

Phase 0 必须先固定：

```text
Association 只能是支持证据
Memory Snapshot 比较模型
Claim 状态与语言模板
报告发布边界
Publication Manifest
双入口合同
Active Lock allocation 状态
StateStore API
WAL 恢复规则
状态映射
```

---

# 24. Phase A 文件建议

## 24.1 Phase A1

```text
orchestrator/agents/
├─ comparison_evidence_agent.py
├─ claim_gate_agent.py
├─ growth_report_agent.py
└─ memory_report_agent.py

现有文件的最小兼容修改范围：
├─ orchestrator/agents/engineering_report_agent.py
├─ orchestrator/agents/growth_analysis_agent.py
├─ orchestrator/agents/memory_agent.py
├─ orchestrator/agents/association_agent.py
├─ orchestrator/agents/visualization_agent.py
└─ orchestrator/agents/final_report_agent.py

docs/
├─ inspection_claim_policy.md
└─ inspection_comparison_evidence_contract.md
```

A1 对现有 Agent 只允许增加“不改变默认 legacy 行为”的显式输出路径/报告模式参数，并按 2.4.1 的映射关闭正式写入。不得在 A1 修改 `config/dag.yaml`、`orchestrator/registry.py`、评分、聚合、Growth 数值公式或默认 CLI 发布行为。A1 只在 sandbox profile 中建立 Run-local Evidence/Claim 和受门控 Staging 报告，不发布到正式 `outputs/`。

## 24.2 Phase A2

```text
orchestrator/inspection_workflow/
└─ publication.py

docs/
└─ inspection_publication_contract.md
```

A2 只实现并在临时项目中验证 publication transaction；在 A3 接入 Active Run Lock 前，不接管 legacy CLI 的正式发布。

## 24.3 Phase A3

```text
orchestrator/inspection_workflow/
├─ controller.py
├─ request.py
├─ planning.py
├─ locking.py
└─ models.py

orchestrator/state/store.py
# 在现有文件中演进 StateStore

config/dag.yaml
# 注册已验收的 A1 节点，仍是唯一业务 DAG

orchestrator/registry.py
# 注册已验收的 A1 Agent，不新增第二套 Registry

scripts/
├─ run_inspection_workflow.py
└─ update_semantic_snapshots.py

docs/
├─ inspection_agent_contract.md
├─ inspection_state_store_contract.md
└─ inspection_engineering_agent.md
```

A3 才接通 Prepared/Legacy 双入口、Active Run Lock、Canonical State/CAS/WAL 和已验收的 Publication。

不得新增重复 Executor、Registry、RunManager 或 DAG Builder。

---

# 25. Phase A 必测

1. `association_supported` 不等于身份确认。
2. 规则关联下 Difference/Directional 最高为 `allowed_with_limits`。
3. 只有 Human/GT verified 才允许无条件“同一病害”文案。
4. 存在历史对象时 `previous_entity_type=memory_snapshot`；无历史对象的 Static Audit 使用 `not_applicable` 且不伪造 Memory。
5. Schema 不存在 `previous_observation_id`。
6. Association Manifest 可定位唯一 `memory_before_query.csv`。
7. matched 分支的 Memory ID 缺失或不唯一时阻断；baseline/unmatched 空 Memory ID 不误判。
8. Registration 默认 `not_verified`。
9. Scale 默认 `false`。
10. Uncertainty 默认 `not_available`。
11. Visualization 实际读取并验证 ClaimDecision。
12. FinalReport 实际读取并验证 ClaimDecision。
13. Memory 正式报告晚于 Claim Gate。
14. history-only Memory 内部报告不输出未门控趋势。
15. 旧成功报告不会被新失败 Run 当作当前结果。
16. Publication Manifest 只在全部验证成功后更新。
17. 逐文件发布失败可以完整回滚。
18. 回滚不完整时隔离 Manifest 并产生恢复标记。
19. Legacy Simulated 不伪装成 Prepared Dataset。
20. 两入口使用同一 Active Lock、StateStore、DAGExecutor 和 Publication。
21. 保留 `run_NNN`。
22. Allocation 阶段崩溃可恢复。
23. `DAGExecutor._checkpoint()` 使用 `checkpoint_context()`。
24. 顶层状态只由 `transition_status()` 修改。
25. metadata 状态映射正确。
26. WAL 最后一行截断可恢复。
27. WAL 中间损坏 Fail Closed。
28. CLI 在 tmp_path 副本中真实执行。
29. Snapshot 测试不能自动更新。
30. Publication Commit 和 final_summary 早于 COMPLETED。
31. Association 阈值、Memory 语义和 Growth 数值公式不变。
32. Artifact Isolation 的 added/removed/modified 全为空。
33. comparability/observation source 缺失或非法时 Fail Closed；只有 schema 合法的明确非 verified 状态才允许 Static Audit。
34. Phase A 的 Physical、Multi-timepoint 和 Prediction capability 始终 blocked。
35. Comparison Evidence 与 ClaimDecision 只生成在当前 Run 的 `artifacts/`，下游不读取旧 `outputs/`。
36. final_summary 和全部必要文件早于 Publication Manifest 提交点。
37. Manifest 已提交但状态未 COMPLETED 的崩溃可以幂等完成或隔离回滚。
38. `allocation_token` 出现在 Run metadata 首次写入中，不存在二次 patch 窗口。
39. A1/A2/A3 分段独立验收，前一段存在 P0/P1 时阻止后一段。
40. Phase A 不声称现有 Web 已接入 canonical state 或 Publication Manifest。
41. A1 专项执行前后，正式 `data/simulated/`、`outputs/` 和 progressive 产物 added/removed/modified 均为空。
42. Manifest artifacts 与 Staging 待发布文件集合完全一致，包含全部可视化逐文件 Hash 和 ClaimDecision 镜像。
43. Manifest 提交后发生 state 冲突时，`final_summary.md` 被隔离到 transaction-scoped 文件并标记 invalidated，已存在时 Fail Closed。
44. Run 目录创建后、metadata 首次写入前崩溃时，可通过锁中的 `reserved_run_id` 定位并 Fail Closed 恢复。
45. `checkpoint_context()` 与 `transition_status()` 均写入同一 State Journal，pending checkpoint 可幂等恢复。
46. 不可比较的 ClaimDecision 缺少受控静态审计限定语时不得发布。
47. Comparison Evidence 不依赖 `label_disease_id` 定位当前生产记录；删除评估标签后 Claim 结果保持一致。
48. header-only baseline 仍按当前 Engineering/Frame 主表生成 Static Audit 和 ClaimDecision。
49. 非 baseline query 缺 Association 行进入 Invalid，不得伪装成 Current-only。
50. `insufficient_history` 不会被可比性合成规则覆盖为 `not_longitudinally_comparable`。
51. `previous_entity_type=not_applicable` 的 CSV/JSON nullable 字段严格使用 canonical 表示。
52. A1 合入后默认 legacy CLI 不注册、不调度 A1 新节点；只有 sandbox profile 可直接测试组件。
53. Manifest source artifact 集合明确包含 final_summary，并与实际路径集合相等。
54. 首次发布失败会删除 `existed_before=false` 的全部新正式文件。
55. replace/delete/restore 后对每个受影响目标父目录执行支持范围内的 fsync。
56. `reserved_run_id=null` 的过期 allocating lock 可按合同恢复，异常目录 Fail Closed。
57. Controller resume 和每个 StateStore mutation 均先恢复或拒绝 unresolved pending。

---

# 26. Phase A 验收命令

Plan-only：

```bash
python scripts/run_inspection_workflow.py \
  --task-file tests/fixtures/inspection_workflow/task_valid.json \
  --plan-only
```

Prepared Workflow：

```bash
python scripts/run_inspection_workflow.py \
  --task-file tests/fixtures/inspection_workflow/task_valid.json
```

Legacy CLI：

```bash
python run.py --mode full_pipeline
```

Artifact Validation：

```bash
python scripts/validate_artifacts.py --project-root .
```

重点测试：

```bash
python -m pytest \
  tests/test_inspection_workflow_request.py \
  tests/test_inspection_workflow_planning.py \
  tests/test_inspection_workflow_locking.py \
  tests/test_inspection_workflow_state_store.py \
  tests/test_inspection_workflow_claim_gate.py \
  tests/test_inspection_comparison_evidence.py \
  tests/test_growth_evidence_reporting.py \
  tests/test_memory_report_claim_gate.py \
  tests/test_publication_transaction.py \
  tests/test_inspection_workflow_artifact_isolation.py \
  tests/test_full_pipeline_claim_gate_migration.py \
  tests/test_cli_sandbox_compatibility.py \
  tests/test_semantic_snapshots.py \
  tests/test_inspection_workflow_e2e.py \
  -q -p no:cacheprovider
```

快速回归：

```bash
python -m pytest -q -m "not slow" -p no:cacheprovider
```

全量回归：

```bash
python -m pytest -q -p no:cacheprovider
```

---

# 27. 停止条件

任一发生，不得宣布 Phase A 完成：

```text
仍把 matched 写成身份确认
规则关联可以无条件使用“同一病害”
header-only baseline 因无 Association 行而没有 Evidence/ClaimDecision
baseline/unmatched 的空 memory_id 被判为 association_invalid
insufficient_history 被覆盖为 not_longitudinally_comparable
不可纵向比较记录可以产生 Difference/Directional Claim
observation_source/comparability_status 缺失或非法时仍可生成 Static Audit
Physical/Multi-timepoint/Prediction 在 Phase A 不是固定 blocked
历史值来源不是明确 Memory Snapshot
当前值和历史值使用不同面积口径
仍存在伪造 previous_observation_id 的路径
Registration/Scale/Uncertainty 无 provenance
任一正式报告不读取 ClaimDecision
Memory 人类报告可绕过 Claim Gate
旧报告文件存在即可被视为当前结果
缺少 Publication Manifest
Comparison Evidence/ClaimDecision 存在多个权威生成位置
Comparison Evidence 依赖 label_disease_id 作为生产连接键
final_summary 或必要文件晚于 Publication Manifest 提交
Manifest 未穷举全部 Staging 发布文件或未展开可视化逐文件 Hash
Manifest source artifact 期望集合不包含 final_summary
Manifest 提交后崩溃没有恢复规则
失败 Run 仍保留成功式 final_summary
回滚时不删除 existed_before=false 的本次新增正式文件
跨 outputs/data/runs 发布后只 fsync 单个目录
发布失败不能回滚或隔离 Manifest
Legacy 模式伪装成 Prepared Dataset
新旧入口不共享锁/StateStore/DAG/Publication
无理由改用 ULID
allocation_token 不是 Run metadata 首次写入内容
Run 目录创建前锁内没有 reserved_run_id，导致无 metadata 崩溃目录无法归属
reserved_run_id=null 的过期 allocating lock 没有恢复规则
DAGExecutor 仍直接写两套状态真相
Context Checkpoint 与 Status Transition 混用
checkpoint_context 不进入统一 State Journal 或没有幂等恢复规则
Controller/StateStore mutation 在 unresolved pending 恢复前继续写入
WAL 截断/损坏规则未实现
CLI 测试写入真实仓库
Snapshot 可自动重录
跳过 A1/A2/A3 分段验收
A1 专项执行修改正式 data/simulated、outputs 或 progressive 产物
A1 新节点在默认 legacy CLI 中提前启用
COMPLETED 早于 Publication Commit 或 final_summary
Semantic Snapshot 显示核心算法漂移
Artifact Validator、重点测试或快速回归失败
```

---

# 28. 后续阶段

## Phase B

```text
完整 Run-local Artifact
ArtifactResolver
Safe Reuse
Stale Detection
显式 Resume
```

## Phase C

```text
Association 人工 Review
human_verified 状态
Review Decision Hash
WAITING_FOR_REVIEW 释放锁
Resume 重新获取锁
```

## Phase D

```text
真实双轮 Pilot
配准与尺度证据
独立 Ground Truth
```

## Phase E

```text
Web Review Console
Manifest 驱动的当前发布结果
```

## Phase F

```text
可选 LLM 文案层
不参与 Claim、Association、Memory 或有效性决策
```

---

# 29. 最终自检

宣布 Phase A 整体验收通过前，以下答案必须全部为“是”；进入 A1/A2/A3 中任一后续阶段时，只要求属于已完成前置阶段的对应问题为“是”：

1. 是否不再把规则匹配写成身份确认？
2. 由 `association_supported` 支持的 Difference/Directional Claim 是否最高只能为 `allowed_with_limits`？
3. Previous Value 是否明确来自 `memory_before_query.csv` 的 Memory Snapshot？
4. 是否禁止伪造 `previous_observation_id`？
5. Registration、Scale、Uncertainty 是否都有 Provenance？
6. observation_source 与 comparability_status 是否进入 Evidence 和 Claim Policy？
7. 缺失/非法 observation 或 comparability 是否 Fail Closed，明确不可比较记录是否只能进行 Static Audit？
8. baseline/current-only 是否由 Engineering/Frame 主表产生 Evidence，且不要求 Association 行或 Memory ID？
9. `insufficient_history` 是否优先于不可比较合成分支？
10. Phase A 的 Physical/Multi-timepoint/Prediction 是否固定 blocked？
11. Visualization 是否实际读取 ClaimDecision？
12. FinalReport 是否实际读取 ClaimDecision？
13. Memory 正式报告是否晚于 Claim Gate？
14. 内部 Memory Snapshot 报告是否使用中性模板？
15. Manifest 提交前失败是否不会更新 Publication Manifest？
16. Manifest 提交后崩溃是否有幂等完成或隔离回滚规则？
17. Manifest 是否穷举全部发布文件、展开可视化逐文件 Hash，并与 Staging/source/final_summary 集合完全一致？
18. 回滚是否恢复旧目标并删除 existed_before=false 的新目标？
19. 是否 fsync 每个受影响的正式父目录？
20. 当前正式报告是否由 Manifest 与 Hash 唯一标识？
21. Legacy Simulated 是否不伪装成 Prepared Dataset？
22. 新旧入口是否共享同一锁、StateStore、DAG 和发布流程？
23. 是否保留 `run_NNN`？
24. allocation_token 是否在 Run metadata 首次写入中出现？
25. Run 目录创建前，Allocation Lock 是否已持久化 reserved_run_id？
26. `reserved_run_id=null` 的 stale lock 是否有无歧义恢复规则？
27. StateStore 是否区分 Context Checkpoint 和 Status Transition？
28. 两类写入是否都进入同一 State Journal 并支持幂等恢复？
29. Controller 与 mutation API 是否在新写入前恢复或拒绝 unresolved pending？
30. WAL 是否处理末尾截断、中间损坏和冲突 Commit？
31. CLI 测试是否在 tmp_path 项目副本中运行？
32. Snapshot 是否禁止测试自动更新？
33. final_summary 和必要文件是否早于 Publication Manifest 提交？
34. 失败 Run 的 final_summary 是否使用 transaction-scoped 名称隔离且禁止覆盖？
35. Publication Commit 与 final_summary 是否早于 COMPLETED？
36. A1 是否通过 Run-local 路径覆盖避免修改正式 data/outputs/progressive？
37. A1 新节点是否在默认 legacy CLI 中保持禁用？
38. A1/A2/A3 是否逐段完成 review 和验收？
39. Comparison Evidence 是否不依赖 label_disease_id 作为生产连接键？
40. 是否明确 Phase A 未改造现有 Web reader？

整体验收时任一为“否”，不得宣布 Phase A 完成；分段实施时，前置阶段相关问题任一为“否”，不得进入下一段。

---

# 30. 最终项目表述

Phase A 实施并验收后可表述：

> 本项目在现有 DAG Orchestrator 基础上增加了 Prepared Dataset 就绪检查、基于现有 DAG 的确定性规划、原子运行控制、history-only no-id 规则关联证据、逐记录 Claim Capability 门控以及 Manifest 驱动的正式报告发布事务，形成了可审计的规则型工程 Agent 最小闭环。

必须持续声明：

> 规则 Association 只提供候选关联支持，不等于真实身份确认；两次观测不能称为趋势；像素差异不等于真实物理变化；方向性变化依赖配准证据，多时点模式至少需要三个有效时点，预测能力需要独立验证和不确定性评估。
