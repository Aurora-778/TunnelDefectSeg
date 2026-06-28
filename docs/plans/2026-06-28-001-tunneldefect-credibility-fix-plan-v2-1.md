# TunnelDefect 项目可信度收束修复计划 v2.1

## 0. 执行原则

本轮不要继续加平台功能，不要引入新模型，不要做大规模重构。  
本轮目标不是让项目“更复杂”，而是让项目“更可信”。

当前项目已经能跑通 full pipeline，核心问题不是功能缺失，而是：

1. `disease_id` 可能参与真实 matching；
2. Association 规则缺少解释；
3. batch memory 和 incremental memory 边界不清；
4. progressive evaluation 没有成为可信度评估核心；
5. schema 校验还不够严格；
6. README / final report 表述可能被理解成真实工业预测系统。

本轮修复必须围绕：

> no-id matching、可解释 association、可信 progressive evaluation、语义 schema 校验、边界清楚的 final report。

---

# 1. 总体修复策略

当前系统已经是一个可运行的工程闭环，不应该继续增加新的 Agent、平台模块或深度模型。  
本轮应优先修复可信度问题：让主 pipeline 的 association 默认不使用 `disease_id`，让 `disease_id` 只作为 evaluation label；让 with-id 结果只作为 upper-bound / sanity check；让 progressive evaluation 成为评估是否存在 temporal leakage 的核心流程；让 README 和 final report 明确 KICT 静态数据和仿真巡检元数据的边界。

最终目标：

```text
能跑的工程 demo
→ 逻辑自洽、边界清楚、可验证、可答辩的工程闭环系统
```

---

# 2. 修复优先级

## P0：必须先修

### P0-1：主 pipeline 默认 no-id matching

#### 问题

主 pipeline 的 association 可能默认启用 `use_disease_id_score=True`，这会让仿真 `disease_id` 参与匹配评分，削弱“跨巡检重新关联”的可信度。

#### 修改目标

- 主 pipeline 默认 `use_disease_id_score: false`；
- `disease_id` 只能作为 `label_disease_id` / evaluation label；
- 主输出 `disease_association_records.csv` 必须是 no-id 结果；
- association 输出中必须显式标记：

```text
association_mode=no_id
use_disease_id_score=false
```

#### no-id 模式硬约束

当 `use_disease_id_score=false` 时：

- scoring 不得使用 `disease_id`；
- candidate ranking 不得使用 `disease_id`；
- match_type 判断不得使用 `disease_id`；
- confidence_level 判断不得使用 `disease_id`；
- conflict_reason 判断不得使用 `disease_id`。

`disease_id` 只能作为 `label_disease_id` 写入最终输出，用于 evaluation / debug / 对比，不得参与真实 matching。

#### 涉及文件

```text
config/dag.yaml
orchestrator/agents/association_agent.py
tests/test_association_agent.py
tests/test_full_pipeline_runner.py
```

#### 验收方式

```bash
python run.py --mode full_pipeline
python -m pytest tests/test_association_agent.py -q
python -m pytest tests/test_full_pipeline_runner.py -q
```

并确认：

```text
data/simulated/disease_association_records.csv
```

中存在：

```text
use_disease_id_score=false
association_mode=no_id
```

---

### P0-2：with-id / no-id 对比只放在 evaluation，不污染主 pipeline

#### 问题

需要对比 with-id 和 no-id，但不能让主 DAG 同时承担两套 association 输出，否则主 pipeline 会变复杂。

#### 修改目标

主 pipeline：

```text
disease_association_records.csv = no-id 正式结果
```

progressive evaluation / evaluation script：

```text
disease_association_records_no_id.csv
disease_association_records_with_id.csv
outputs/association_evaluation_report.md
```

其中：

- no-id 是 primary evaluation；
- with-id 是 upper-bound / sanity check；
- with-id 不能进入主业务结论。

#### Progressive Evaluation 防泄漏约束

progressive evaluation 不能直接使用 full pipeline 生成的全量 `disease_memory_bank.csv` 作为初始 memory。

必须按 inspection 时间顺序执行：

1. 使用历史 inspection 构建 / 更新 memory；
2. 用当前 inspection 做 no-id association；
3. association 完成后，再把当前 inspection 写入 memory；
4. 进入下一轮 inspection。

这样才能避免 memory 提前看到未来巡检数据。

#### 涉及文件

```text
scripts/run_progressive_inspection_evaluation.py
orchestrator/agents/final_report_agent.py
tests/test_progressive_evaluation.py
tests/test_association_no_id_vs_with_id.py
```

#### 验收方式

```bash
python scripts/run_progressive_inspection_evaluation.py
python -m pytest tests/test_progressive_evaluation.py -q
python -m pytest tests/test_association_no_id_vs_with_id.py -q
```

必须生成：

```text
data/simulated/disease_association_records_no_id.csv
data/simulated/disease_association_records_with_id.csv
outputs/association_evaluation_report.md
```

---

### P0-3：Association 规则必须可解释

#### 问题

当前 association 使用 spatial / area / temporal / risk 等规则评分，但权重和阈值缺少解释，容易被认为是拍脑袋。

#### 修改目标

新增：

```text
docs/association_rule_design.md
```

文档必须说明：

- 每个 score 的定义；
- 每个权重的工程含义；
- hard / soft / uncertain / unmatched 的划分；
- `score_margin` 如何触发 manual review；
- no-id 与 with-id 的区别；
- 为什么这是 rule-based baseline，不是学习型重识别算法；
- 当前限制是什么。

#### 涉及文件

```text
docs/association_rule_design.md
README.md
docs/artifact_contract.md
orchestrator/agents/final_report_agent.py
```

#### 验收方式

人工检查文档存在，并且 README / final report 中引用该文档或包含对应说明。

---

### P0-4：batch_rebuild 与 incremental_update 边界必须写清

#### 问题

主 pipeline 使用 `batch_rebuild`，progressive evaluation 才模拟 `incremental_update`。如果不说明，容易把 batch memory 误讲成真实长期在线记忆。

#### 修改目标

在 README、final report、system summary 中明确：

- `batch_rebuild` 是全量重建摘要；
- `incremental_update` 才更接近真实巡检；
- 当前 Memory Bank 是工程化记忆表；
- 不是从视觉数据中自动学习出的真实长期身份追踪系统；
- KICT 是静态 mask，巡检元数据是仿真，不能证明真实长期演化。

#### 涉及文件

```text
orchestrator/agents/memory_agent.py
orchestrator/agents/final_report_agent.py
README.md
docs/artifact_contract.md
```

#### 验收方式

运行：

```bash
python run.py --mode full_pipeline
```

检查：

```text
outputs/final_project_report.md
outputs/system_summary.md
data/simulated/disease_memory_bank.csv
```

必须出现：

```text
batch_rebuild
incremental_update
KICT static masks
simulated inspection metadata
not real longitudinal evidence
```

---

## P1：推荐修

### P1-1：schema 从字段校验升级到语义校验

#### 问题

当前 schema validation 主要检查字段和 enum，不能抓住 score 越界、bool 非法、count 非法等问题。

#### 修改目标

扩展：

```text
orchestrator/schema.py
scripts/validate_artifacts.py
tests/test_artifact_schema_validation.py
```

新增语义校验：

- score 字段必须在 `[0, 1]`；
- `score_margin` 必须非负；
- `candidate_count` 必须是非负整数；
- `needs_manual_review` / `use_disease_id_score` 必须是合法 bool；
- `memory_version` 必须以 `v` 开头；
- 可选 strict 模式检查 path 字段是否存在。

#### 涉及文件

```text
orchestrator/schema.py
scripts/validate_artifacts.py
tests/test_artifact_schema_validation.py
docs/artifact_contract.md
```

#### 验收方式

```bash
python scripts/validate_artifacts.py --project-root .
python -m pytest tests/test_artifact_schema_validation.py -q
```

---

### P1-2：几何特征先检查，不强行实现

#### 问题

Association 缺少 mask / bbox / shape 几何特征，但如果当前 artifact 没有这些字段，强行实现会破坏 pipeline。

#### 修改目标

本轮不要强行加入复杂几何特征。  
先做字段检查和 limitation 显式化：

- 如果现有 CSV 已有 bbox / shape 字段，可以接入轻量几何 score；
- 如果没有，不要硬造；
- 输出：

```text
geometry_feature_available=false
geometry_limit_note=missing bbox/mask shape fields in current artifacts
```

并在文档中说明这是后续增强方向。

#### Association Schema 字段分层

Required fields：

```text
frame_id
inspection_id
label_disease_id
matched_disease_id
association_status
association_score
confidence_level
match_type
candidate_count
top_candidate_ids
score_margin
conflict_reason
needs_manual_review
use_disease_id_score
association_mode
geometry_feature_available
geometry_limit_note
```

Optional geometry fields：

```text
bbox_center_distance_score
bbox_size_similarity_score
aspect_ratio_similarity_score
shape_proxy_score
```

如果当前 artifact 没有 bbox / mask shape 字段，optional geometry fields 可以不存在，但必须输出：

```text
geometry_feature_available=false
geometry_limit_note=missing bbox/mask shape fields in current artifacts
```

#### 涉及文件

```text
orchestrator/agents/association_agent.py
orchestrator/schema.py
docs/association_rule_design.md
docs/artifact_contract.md
README.md
```

#### 验收方式

`disease_association_records.csv` 中应包含：

```text
geometry_feature_available
geometry_limit_note
```

但不能强制要求 bbox 相关分数字段一定存在。

---

### P1-3：progressive evaluation 进入 final report，但不能强依赖

#### 问题

`outputs/association_evaluation_report.md` 可能不是每次 full pipeline 都提前生成。如果 final report 强依赖它，会导致 full pipeline 不稳定。

#### 修改目标

`FinalReportAgent` 应该：

```text
如果 association_evaluation_report.md 存在：
    插入 progressive evaluation 摘要
否则：
    写“尚未运行 progressive evaluation”
    给出运行命令
```

不要让 final report 因为 evaluation report 缺失而失败。

#### 涉及文件

```text
orchestrator/agents/final_report_agent.py
tests/test_full_pipeline_runner.py
```

#### 验收方式

只运行：

```bash
python run.py --mode full_pipeline
```

即使没有提前运行 progressive evaluation，也必须成功生成 final report。

---

### P1-4：测试分层放到最后做

#### 问题

全量测试可能较慢，不利于 Codex 每次 patch 后快速验证。

#### 修改目标

新增 pytest markers：

```text
quick
integration
slow
```

但这不是 P0，不要影响前面核心修复。

#### 涉及文件

```text
pytest.ini
tests/*
```

#### 验收方式

```bash
python -m pytest -m quick -q
```

---

## P2：后续优化，不作为本轮硬要求

### P2-1：run artifacts 按 task 隔离

规划但不强改：

```text
runs/run_xxx/artifacts/engineering_report/
runs/run_xxx/artifacts/growth_analysis/
runs/run_xxx/artifacts/memory/
runs/run_xxx/artifacts/association/
runs/run_xxx/artifacts/visualization/
runs/run_xxx/artifacts/final_report/
```

短期不动全局输出路径，避免破坏现有 pipeline。

---

### P2-2：orchestrator/run.py 默认 DAG 暂缓修改

本轮不强制修改默认行为。  
只要求 README 明确推荐：

```bash
python orchestrator/run.py --dag config/dag.yaml
```

等 P0 稳定后，再考虑默认走 DAG。

---

# 3. 逐文件修改计划

## File: `config/dag.yaml`

### 当前问题

主 association 可能没有显式禁用 `disease_id score`。

### 修改目标

主 pipeline 默认 no-id matching。

### 具体修改点

在 association 输入配置中加入：

```yaml
use_disease_id_score: false
association_mode: no_id
```

不要在主 DAG 里加入 with-id association 任务。  
with-id 只放到 progressive evaluation / evaluation script 中生成。

### 验收标准

```bash
python run.py --mode full_pipeline
```

检查：

```text
data/simulated/disease_association_records.csv
```

必须包含：

```text
use_disease_id_score=false
association_mode=no_id
```

---

## File: `orchestrator/agents/association_agent.py`

### 当前问题

Association 虽已有规则评分，但仍需确保：

- 默认不使用 `disease_id_score`；
- `disease_id` 只作为 label；
- no-id 模式下 `disease_id` 不得进入任何计算分支；
- 输出能解释当前 matching 是 no-id 还是 with-id；
- 不可因为同 ID 覆盖空间冲突；
- 几何特征缺失时要显式说明。

### 修改目标

把 AssociationAgent 收束为：

```text
rule-based, no-id-default, explainable association baseline
```

### 具体修改点

#### 1. 新增 bool parser

新增：

```python
def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "y"}
```

使用：

```python
use_disease_id_score = parse_bool(inputs.get("use_disease_id_score"), default=False)
```

#### 2. 默认禁用 id score

如果配置缺失，必须默认 False。

#### 3. no-id 模式硬约束

当 `use_disease_id_score=false` 时：

- scoring 不得使用 `disease_id`；
- candidate ranking 不得使用 `disease_id`；
- match_type 判断不得使用 `disease_id`；
- confidence_level 判断不得使用 `disease_id`；
- conflict_reason 判断不得使用 `disease_id`。

`disease_id` 只能作为 `label_disease_id` 写入最终 CSV。

#### 4. 输出模式字段

每条 association 记录新增：

```text
use_disease_id_score
association_mode
label_disease_id
```

值：

```text
use_disease_id_score=false
association_mode=no_id
```

或：

```text
use_disease_id_score=true
association_mode=with_id_upper_bound
```

#### 5. 防止 same-id 覆盖空间冲突

即使 `use_disease_id_score=true`，也不能出现：

```text
same disease_id + spatial conflict → hard match
```

如果空间冲突严重：

```text
match_type=uncertain
needs_manual_review=true
conflict_reason includes spatial mismatch
```

#### 6. 几何特征可用性字段

如果没有 bbox / mask shape 字段，输出：

```text
geometry_feature_available=false
geometry_limit_note=missing bbox/mask shape fields in current artifacts
```

不要硬造几何分数。

#### 7. 输出字段必须包含

```text
association_score
spatial_distance_score
area_similarity_score
temporal_continuity_score
risk_similarity_score
candidate_count
top_candidate_ids
score_margin
conflict_reason
needs_manual_review
use_disease_id_score
association_mode
label_disease_id
geometry_feature_available
geometry_limit_note
```

### 验收标准

```bash
python -m pytest tests/test_association_agent.py -q
```

---

## File: `scripts/run_progressive_inspection_evaluation.py`

### 当前问题

progressive evaluation 需要成为 no-id 可信评估核心，并生成 with-id / no-id 对比。

### 修改目标

生成：

```text
data/simulated/disease_association_records_no_id.csv
data/simulated/disease_association_records_with_id.csv
outputs/association_evaluation_report.md
```

### 具体修改点

#### 1. 防止 future memory leakage

progressive evaluation 不能直接读取 full pipeline 生成的全量 `disease_memory_bank.csv` 作为初始 memory。

必须按 inspection 时间顺序执行：

```text
历史 inspection 更新 memory
→ 当前 inspection 做 no-id association
→ association 完成后当前 inspection 才写入 memory
→ 下一轮
```

#### 2. no-id 为主评估

默认跑：

```python
use_disease_id_score=False
```

输出：

```text
data/simulated/disease_association_records_no_id.csv
```

#### 3. with-id 为 upper-bound

额外跑：

```python
use_disease_id_score=True
```

输出：

```text
data/simulated/disease_association_records_with_id.csv
```

#### 4. 生成 markdown 对比报告

报告必须包含：

```text
no-id primary evaluation
with-id upper-bound / sanity check
matched rate
uncertain count
manual review count
difference between no-id and with-id
temporal leakage warning
```

#### 5. 不改变主 pipeline 输出

不要让该脚本覆盖：

```text
data/simulated/disease_association_records.csv
```

### 验收标准

```bash
python scripts/run_progressive_inspection_evaluation.py
```

---

## File: `orchestrator/agents/memory_agent.py`

### 当前问题

batch memory 可能被误解成真实长期记忆。

### 修改目标

Memory 输出明确模式和限制。

### 具体修改点

确保 memory bank 字段包含：

```text
memory_update_mode
memory_confidence
memory_limit_note
source_record_count
source_inspection_ids
requires_manual_review
```

batch 模式写：

```text
memory_update_mode=batch_rebuild
memory_limit_note=Batch rebuilt from full simulated artifacts; not real online visual identity memory.
```

incremental 模式写：

```text
memory_update_mode=incremental_update
```

### 验收标准

```bash
python run.py --mode full_pipeline
```

检查：

```text
data/simulated/disease_memory_bank.csv
outputs/memory_agent_report.md
```

---

## File: `orchestrator/agents/final_report_agent.py`

### 当前问题

final report 需要强化边界说明，并优雅处理 progressive evaluation 报告不存在的情况。

### 修改目标

final report 必须边界清楚，不夸大。

### 具体修改点

#### 1. 新增数据边界小节

```markdown
## 数据边界与结论适用范围

当前系统使用 KICT 静态裂缝 mask 与仿真机器人巡检元数据。
系统验证的是端到端工程流程，不证明真实隧道病害长期演化规律。
```

#### 2. 新增 Memory 模式说明

```markdown
## Memory Bank 模式说明

主 pipeline 使用 batch_rebuild。
progressive evaluation 使用 incremental_update。
二者不能混为真实在线长期追踪。
```

#### 3. 新增 Association 可信度说明

```markdown
## Association 可信度说明

主匹配默认不使用 disease_id score。
disease_id 仅用于 label/evaluation。
with-id 结果仅作为 upper-bound / sanity check。
```

#### 4. progressive evaluation 可选插入

逻辑：

```text
if outputs/association_evaluation_report.md exists:
    insert summary
else:
    write "Progressive evaluation has not been run yet."
    include command: python scripts/run_progressive_inspection_evaluation.py
```

#### 5. 替换夸大措辞

避免：

```text
真实增长预测
长期演化建模
工业级上线
视觉重识别算法
```

替换为：

```text
规则面积变化提示
仿真巡检评估
rule-based association baseline
memory-aware defect analysis prototype
```

### 验收标准

```bash
python run.py --mode full_pipeline
```

检查：

```text
outputs/final_project_report.md
outputs/system_summary.md
outputs/key_insights.md
```

必须包含：

```text
KICT static
simulated inspection metadata
batch_rebuild
incremental_update
disease_id
progressive evaluation
```

---

## File: `orchestrator/schema.py`

### 当前问题

schema 主要是字段级，缺少语义校验；几何字段不应全部强制 required。

### 修改目标

增加数值、bool、整数、版本格式校验，并区分 required / optional 字段。

### 具体修改点

#### 1. Association required fields

```text
frame_id
inspection_id
label_disease_id
matched_disease_id
association_status
association_score
confidence_level
match_type
candidate_count
top_candidate_ids
score_margin
conflict_reason
needs_manual_review
use_disease_id_score
association_mode
geometry_feature_available
geometry_limit_note
```

#### 2. Association optional geometry fields

```text
bbox_center_distance_score
bbox_size_similarity_score
aspect_ratio_similarity_score
shape_proxy_score
```

#### 3. score 字段校验

以下字段如果存在，必须满足：

```text
0 <= value <= 1
```

```text
association_score
spatial_distance_score
area_similarity_score
temporal_continuity_score
risk_similarity_score
bbox_center_distance_score
bbox_size_similarity_score
aspect_ratio_similarity_score
shape_proxy_score
```

#### 4. score_margin 校验

只要求：

```text
score_margin >= 0
```

不要强制 `score_margin <= 1`。

#### 5. 整数字段校验

```text
candidate_count
source_record_count
inspection_count
total_seen_frames
```

必须是非负整数。

#### 6. bool 字段校验

```text
needs_manual_review
requires_manual_review
use_disease_id_score
geometry_feature_available
```

允许：

```text
true
false
True
False
0
1
```

建议输出统一小写：

```text
true / false
```

#### 7. memory_version 校验

最小规则：

```python
memory_version.startswith("v")
```

#### 8. 保持函数返回 error list

```python
def validate_csv_schema(path: Path, schema_name: str) -> list[str]:
    ...
```

不要直接 print。

### 验收标准

```bash
python -m pytest tests/test_artifact_schema_validation.py -q
```

---

## File: `scripts/validate_artifacts.py`

### 当前问题

需要接入语义 schema 校验，并清晰输出错误。

### 修改目标

把语义错误纳入 artifact validation。

### 具体修改点

调用：

```python
errors = validate_csv_schema(path, schema_name)
```

输出格式：

```text
[OK] disease_memory_bank.csv
[ERROR] disease_association_records.csv
  - association_score out of range: 1.4
  - needs_manual_review invalid bool: maybe
```

可选支持：

```bash
python scripts/validate_artifacts.py --project-root . --strict
```

strict 模式可检查 path 字段是否存在。

### 验收标准

```bash
python scripts/validate_artifacts.py --project-root .
```

必须通过。

---

## File: `docs/artifact_contract.md`

### 当前问题

需要和真实输出字段同步，并加入语义约束。

### 修改目标

artifact contract 成为 schema.py 的文档版。

### 具体修改点

association required fields：

```text
frame_id
inspection_id
label_disease_id
matched_disease_id
association_status
association_score
confidence_level
match_type
candidate_count
top_candidate_ids
score_margin
conflict_reason
needs_manual_review
use_disease_id_score
association_mode
geometry_feature_available
geometry_limit_note
```

association optional geometry fields：

```text
bbox_center_distance_score
bbox_size_similarity_score
aspect_ratio_similarity_score
shape_proxy_score
```

memory schema 增加：

```text
memory_update_mode
memory_confidence
memory_limit_note
source_record_count
source_inspection_ids
requires_manual_review
```

加入语义约束：

```text
association_score in [0, 1]
score_margin >= 0
candidate_count non-negative integer
needs_manual_review true/false
```

### 验收标准

人工检查 `docs/artifact_contract.md` 与 `orchestrator/schema.py` 一致。

---

## File: `docs/association_rule_design.md`

### 当前问题

缺少 association 规则说明。

### 修改目标

新增规则设计文档。

### 具体内容

```markdown
# Association Rule Design

## 1. Design Goal
This is a rule-based association baseline, not a learned visual re-identification model.

## 2. Input Signals
- spatial distance
- area similarity
- temporal continuity
- risk similarity
- optional disease_id score only for upper-bound evaluation

## 3. Score Definitions
Explain every score.

## 4. Weight Design
Explain that weights are engineering heuristics, not learned parameters.

## 5. Match Types
- hard
- soft
- uncertain
- unmatched

## 6. Manual Review Rules
Explain score_margin, conflict_reason, needs_manual_review.

## 7. With-ID vs No-ID
No-id is the primary evaluation.
With-id is only upper-bound / sanity check.

## 8. Limitations
- no real continuous inspection GT
- no visual embedding
- no learned re-identification
- limited geometry features
```

### 验收标准

文件存在，README 或 final report 中有引用。

---

## File: `README.md`

### 当前问题

项目表述需要进一步收束。

### 修改目标

README 必须明确项目是工程原型，不是真实预测系统。

### 具体修改点

加入项目定位：

```markdown
本项目是面向机器人隧道巡检场景的 rule-based, memory-aware engineering prototype，用于验证病害对象建模、跨巡检关联、记忆表和报告生成流程。
```

加入数据边界：

```markdown
当前使用 KICT 静态裂缝 mask 与仿真巡检元数据，不证明真实病害长期演化规律。
```

加入流程说明：

```markdown
## Full Pipeline
展示端到端闭环。

## Progressive Evaluation
验证 no-id association 和 memory update 是否存在未来信息泄漏。
```

加入推荐命令：

```bash
python run.py --mode full_pipeline
python scripts/run_progressive_inspection_evaluation.py
python scripts/validate_artifacts.py --project-root .
```

避免出现：

```text
industrial-grade production system
real degradation forecasting
true long-term defect tracking
CVPR-level
```

### 验收标准

README 中必须包含：

```text
rule-based
simulated inspection metadata
KICT static masks
progressive evaluation
no-id association
```

---

## File: `tests/test_association_agent.py`

### 当前问题

测试需要防止假修复。

### 修改目标

测试覆盖 no-id 默认、same-id 冲突、no-id 模式不读 ID。

### 具体新增测试

```python
def test_default_association_disables_disease_id_score(...):
    ...

def test_same_id_does_not_override_spatial_conflict(...):
    ...

def test_no_id_mode_outputs_metadata(...):
    ...

def test_score_fields_are_in_range(...):
    ...

def test_no_id_mode_ignores_same_disease_id_when_features_conflict(...):
    """
    In no-id mode, same disease_id must not improve score or force hard match
    when spatial/area evidence conflicts.
    """
```

### 验收标准

```bash
python -m pytest tests/test_association_agent.py -q
```

---

## File: `tests/test_association_no_id_vs_with_id.py`

### 当前问题

缺少 with-id / no-id 对比测试。

### 修改目标

新增测试文件。

### 具体测试

验证：

- no-id CSV 生成；
- with-id CSV 生成；
- no-id 标记为 primary；
- with-id 标记为 upper-bound；
- 两者不覆盖主 pipeline 的 `disease_association_records.csv`；
- progressive evaluation 不直接使用 full pipeline 的全量 `disease_memory_bank.csv` 作为初始 memory。

### 验收标准

```bash
python -m pytest tests/test_association_no_id_vs_with_id.py -q
```

---

## File: `tests/test_progressive_evaluation.py`

### 当前问题

progressive evaluation 缺测试。

### 修改目标

新增测试验证 evaluation 产物和防泄漏约束。

### 具体测试

检查：

```text
outputs/association_evaluation_report.md
data/simulated/disease_association_records_no_id.csv
data/simulated/disease_association_records_with_id.csv
```

报告必须包含：

```text
no-id
with-id
upper-bound
temporal leakage
progressive
incremental_update
```

### 验收标准

```bash
python -m pytest tests/test_progressive_evaluation.py -q
```

---

## File: `tests/test_artifact_schema_validation.py`

### 当前问题

缺少语义错误测试。

### 修改目标

新增测试：

```python
def test_score_out_of_range_is_rejected(...):
    ...

def test_negative_score_margin_is_rejected(...):
    ...

def test_invalid_bool_string_is_rejected(...):
    ...

def test_negative_candidate_count_is_rejected(...):
    ...

def test_invalid_memory_version_is_rejected(...):
    ...

def test_optional_geometry_fields_are_not_required_when_geometry_unavailable(...):
    ...
```

### 验收标准

```bash
python -m pytest tests/test_artifact_schema_validation.py -q
```

---

## File: `tests/test_full_pipeline_runner.py`

### 当前问题

full pipeline 测试需要确认 no-id 默认和边界说明。

### 修改目标

新增断言：

```python
assert "use_disease_id_score" in association_csv_text
assert "false" in association_csv_text
assert "association_mode" in association_csv_text
assert "no_id" in association_csv_text
assert "KICT" in final_report_text
assert "simulated inspection metadata" in final_report_text
assert "progressive evaluation" in final_report_text.lower()
```

### 验收标准

```bash
python -m pytest tests/test_full_pipeline_runner.py -q
```

---

## File: `pytest.ini`

### 当前问题

测试未分层。

### 修改目标

最后一轮再做，不要影响 P0。

### 具体修改点

新增：

```ini
[pytest]
markers =
    quick: fast unit tests for every patch
    integration: pipeline-level integration tests
    slow: expensive or optional tests
```

给测试加 marker：

- quick：
  - association agent
  - schema validation
  - memory basic
- integration：
  - full pipeline
  - progressive evaluation
- slow：
  - web / image-heavy / full repo tests

### 验收标准

```bash
python -m pytest -m quick -q
```

---

# 4. 防假修复检查表

Codex 修改后必须逐项检查：

```markdown
- [ ] `config/dag.yaml` 中主 association 明确 `use_disease_id_score: false`
- [ ] `AssociationAgent` 默认值是 false，不是 true
- [ ] no-id 模式下 scoring 不读取 `disease_id`
- [ ] no-id 模式下 candidate ranking 不读取 `disease_id`
- [ ] no-id 模式下 match_type / confidence_level 不读取 `disease_id`
- [ ] association 输出包含 `association_mode=no_id`
- [ ] `disease_id` 只作为 `label_disease_id` 或 evaluation 字段存在
- [ ] same disease_id + spatial conflict 不会 hard match
- [ ] progressive evaluation 按 inspection 顺序增量更新 memory
- [ ] progressive evaluation 不直接使用 full pipeline 的全量 memory 作为初始 memory
- [ ] progressive evaluation 生成 no-id / with-id 对比
- [ ] with-id 输出没有覆盖主 pipeline 输出
- [ ] final report 不会在 progressive report 缺失时崩溃
- [ ] schema validator 能抓 score > 1
- [ ] schema validator 能抓非法 bool
- [ ] schema validator 能抓负数 candidate_count
- [ ] schema required / optional 字段分层合理
- [ ] README 明确 KICT 静态 + 仿真元数据边界
- [ ] final report 明确 batch_rebuild ≠ true online memory
- [ ] `docs/association_rule_design.md` 存在
```

---

# 5. 推荐 Codex 执行顺序

不要一次性全改。分 4 轮执行。

## Round 1：no-id matching 真修

修改：

```text
config/dag.yaml
orchestrator/agents/association_agent.py
tests/test_association_agent.py
tests/test_full_pipeline_runner.py
```

验收：

```bash
python run.py --mode full_pipeline
python -m pytest tests/test_association_agent.py -q
python -m pytest tests/test_full_pipeline_runner.py -q
```

---

## Round 2：with-id / no-id evaluation

修改：

```text
scripts/run_progressive_inspection_evaluation.py
orchestrator/agents/final_report_agent.py
tests/test_progressive_evaluation.py
tests/test_association_no_id_vs_with_id.py
```

验收：

```bash
python scripts/run_progressive_inspection_evaluation.py
python -m pytest tests/test_progressive_evaluation.py -q
python -m pytest tests/test_association_no_id_vs_with_id.py -q
```

---

## Round 3：schema 语义校验

修改：

```text
orchestrator/schema.py
scripts/validate_artifacts.py
tests/test_artifact_schema_validation.py
docs/artifact_contract.md
```

验收：

```bash
python scripts/validate_artifacts.py --project-root .
python -m pytest tests/test_artifact_schema_validation.py -q
```

---

## Round 4：文档与表述收束

修改：

```text
README.md
docs/association_rule_design.md
docs/artifact_contract.md
orchestrator/agents/final_report_agent.py
pytest.ini
```

验收：

```bash
python run.py --mode full_pipeline
python -m pytest -m quick -q
```

---

# 6. 完整验收命令

全部完成后运行：

```bash
python run.py --mode full_pipeline
python orchestrator/run.py --dag config/dag.yaml
python scripts/validate_artifacts.py --project-root .
python scripts/run_progressive_inspection_evaluation.py
python -m pytest tests/test_association_agent.py -q
python -m pytest tests/test_artifact_schema_validation.py -q
python -m pytest tests/test_full_pipeline_runner.py -q
python -m pytest tests/test_association_no_id_vs_with_id.py -q
python -m pytest tests/test_progressive_evaluation.py -q
python -m pytest -m quick -q
```

如果时间允许，再运行：

```bash
python -m pytest -q
```

---

# 7. 本轮完成后的合格表述

修复后，项目应该描述为：

> 一个基于 KICT 静态裂缝 mask 和仿真巡检元数据的 memory-aware tunnel defect analysis prototype。系统通过 DAG pipeline 串联工程报告、Memory Bank、rule-based no-id association、progressive evaluation、artifact validation 和 final report，用于验证机器人巡检病害分析工程闭环。

不要描述为：

```text
真实病害长期演化预测系统
工业级上线平台
CVPR 主会级算法
真实视觉重识别系统
```

---

# 8. 最终 Review

## 总体评价

这版 v2.1 已经可以直接给 Codex 使用，而且比 v2 更安全。

它补上了三个关键保险：

```text
1. no-id 模式下 disease_id 绝不参与任何计算
2. progressive evaluation 不能使用 full pipeline 全量 memory
3. schema required / optional 字段分层
```

这三个点非常关键，因为它们直接防止 Codex 做“表面修复”。

## 评分

| 维度 | 评分 |
|---|---:|
| 方向正确性 | 9.5/10 |
| 可执行性 | 8.5/10 |
| 防假修复能力 | 9/10 |
| 工程风险控制 | 9/10 |
| Codex 友好度 | 8.5/10 |

## 仍然需要注意

不要一次性让 Codex 全部执行。建议只让 Codex 先执行：

```text
Round 1：no-id matching 真修
```

也就是只改：

```text
config/dag.yaml
orchestrator/agents/association_agent.py
tests/test_association_agent.py
tests/test_full_pipeline_runner.py
```

完成后马上运行：

```bash
python run.py --mode full_pipeline
python -m pytest tests/test_association_agent.py -q
python -m pytest tests/test_full_pipeline_runner.py -q
```

然后人工检查 `association_agent.py`：

> 当 `use_disease_id_score=false` 时，`disease_id` 有没有进入 score / candidate ranking / match_type / confidence_level 判断。

## 最终提醒

本轮修复的核心不是“让项目更高级”，而是：

```text
让项目更可信，而不是更复杂。
```
