---
title: TunnelDefect Real Inspection Input Foundation Plan
type: feat
status: active
date: 2026-07-16
origin: docs/brainstorms/2026-07-16-real-inspection-input-foundation-requirements.md
---

# TunnelDefect Real Inspection Input Foundation Plan

## Goal

实现一个单 sequence、无答案泄漏、可事务发布的真实巡检输入适配基础层。它把 `images/ + masks/ + metadata.csv` 转换为中性 observation records、现有 schema 兼容 frame records 和 preparation manifest，并用临时夹具证明产物能直接进入现有 history-only no-id Association。

本阶段完成的是 **fixture-backed input contract validation**，不是完整真实 Pilot，也不产生真实 Association 指标或纵向 Growth 结论。

---

## Non-Goals

- 不实现 GT、人工审核、split、Association evaluator、headline metrics 或 error cases；
- 不接入真实数据包，不下载或提交数据；
- 不修改 `AssociationAgent` 评分、Memory、Growth、`config/dag.yaml`、`run.py` 或主 pipeline；
- 不修改 Web、视频链路、模型推理、训练或部署；
- 不引入 Adapter registry、抽象基类、第二套 coordinator、数据库、LLM、tracker、Hungarian 或 embedding；
- 不修改 `data/simulated/` 或正式 `outputs/`。

---

## Existing Contracts Reused

- `orchestrator.schema.REQUIRED_SCHEMAS["robot_kict_frame_records"]`：兼容 frame records 的既有列合同。
- `orchestrator.schema.validate_csv_schema()`：发布前验证 staging frame CSV。
- `scripts.extract_kict_mask_features.extract_mask_features()` 的半开 bbox 语义：`x2/y2` 为 exclusive，`kict_mask_width/height` 为前景 bbox 尺寸。
- `orchestrator.history_only_association.run_history_only_association()`：只在测试中验证 baseline/query 兼容性，不复制生产协调逻辑。
- `orchestrator.agents.association_agent.AssociationAgent`：只在测试中走现有 no-id history-only 入口，不修改评分或阈值。

现有 coordinator 不识别 `sequence_id`，因此 preparation CLI 每次只接受一个 sequence；多 sequence 通过独立调用和独立 output directory 处理。

---

## Input Contract

`--dataset-root` 必须包含：

```text
dataset-root/
├─ images/
├─ masks/
└─ metadata.csv
```

`metadata.csv` 必需列：

```text
sequence_id
source_inspection_id
frame_id
timestamp
mileage_m
ring_id
clock_direction
image_file
mask_file
local_observation_id
disease_type
```

关键约束：

- 全表只能有一个 `sequence_id`。
- ID 字段遵守 requirements 中的安全字符规则；`frame_id/ring_id` 是非负整数。
- timestamp 是带时区 ISO-8601，归一化为 UTC 后比较与输出。
- image/mask 路径分别限制在 `images/`、`masks/` 内，文件存在且像素尺寸相同。
- 原样写入 CSV 的自由文本字段拒绝 `= + - @` 公式前缀。
- 同帧工程元数据一致；同 inspection 的同一 local observation 保持 disease type 一致。
- inspection 时间区间严格不重叠；输入行顺序不影响 `I0001...` 映射。

---

## Output Contract

CLI 只发布：

```text
<output-dir>/observation_records.csv
<output-dir>/frame_records.csv
<output-dir>/preparation_manifest.json
```

固定语义：

```text
data_contract_version=real_inspection_pilot_v1
observation_source=real_inspection_mask_input
comparability_status=not_longitudinally_comparable
path_base=dataset_root
```

- observation records 保留全部观测；空 mask 使用明确哨兵并标记 `association_eligible=false`。
- frame records 只包含非空 mask，满足现有 `robot_kict_frame_records` schema，并补 `kict_mask_width/height` 与 provenance 列。
- `disease_id` 是 sequence/inspection 内的局部观测 ID，不表达跨轮同一病害。
- 三个产物都不得包含 `global_disease_id` 或本机绝对路径。
- manifest 记录输入文件集合摘要、两个 CSV 的 SHA-256、inspection 映射、逐轮计数和 readiness；manifest 不自哈希。

---

## Implementation Units

### U1. Data contract documentation

**Add** `docs/real_inspection_input_foundation.md`

内容：

- 原始目录、metadata 列和字段类型；
- 单 sequence 限制及多 sequence 独立调用方式；
- UTC、里程文本、半开 bbox、空 mask 和 readiness 语义；
- CLI 示例、三个输出及 overwrite/validate-only 行为；
- `kict_*` 只是兼容投影命名；
- 明确无 GT、无真实指标、无 Growth claim。

不修改 README，避免把基础合同误写成已完成真实 Pilot。

### U2. One preparation CLI

**Add** `scripts/prepare_real_inspection_pilot.py`

保持单文件、纯函数优先，建议只包含以下职责：

1. `parse_args()`：`--dataset-root`、`--output-dir` 为必填参数；`--validate-only`、`--overwrite` 为布尔开关，不提供危险默认输出目录。
2. `load_metadata()`：读取 UTF-8-SIG CSV，校验表头、空表和逐行非空。
3. `validate_and_normalize_rows()`：
   - 验证 ID、数字、clock、UTC timestamp；
   - 安全解析 image/mask 相对路径；
   - 验证复合键、单 sequence、同帧和 disease type 一致性；
   - 校验图像/mask 存在、后缀和尺寸；
   - 按 inspection UTC 时间区间生成稳定 `I0001...` 映射。
4. `extract_mask_geometry()`：按非零像素提取半开 bbox、中心、前景 bbox 尺寸和画布尺寸；空 mask 返回固定哨兵。
5. `build_observation_records()` 与 `build_frame_records()`：构造中性来源和兼容投影；里程按 decimal half-up 归一到 0.1 米后生成 `Kx+xxx.x`。
6. `build_preparation_manifest()`：记录 contract、输入 hashes、计数、mapping、readiness、输出 hashes 和相对路径。
7. `validate_prepared_artifacts()`：
   - 校验字段、版本、无 `global_disease_id`、无绝对路径；
   - 用现有 `validate_csv_schema(..., "robot_kict_frame_records", allow_empty=True)` 检查 staging frame CSV 的表头和已有行；readiness 独立判断，header-only frame CSV 是合法的 not-ready audit artifact；
   - `--validate-only` 在自动清理的临时目录中完成同等 dry-run，不发布项目产物。
8. `require_inference_ready(manifest)`：读取内存或已生成 manifest；`inference_ready!=true` 时抛出包含 readiness reasons 的错误。兼容性 smoke 必须先通过该 gate，不能只在测试代码中自行判断。
9. `publish_artifacts()`：
   - 对 dataset/output/protected roots 使用 `resolve(strict=False)` 与 `Path.relative_to()` 做路径层级判断；拒绝 output root 本身或其任何目标位于 dataset root、项目 `data/simulated/`、正式 `outputs/` 内，并拒绝与输入文件重合；符号链接和等价路径不能绕过；
   - 无 `--overwrite` 时任何已知目标存在即失败；
   - 在 output directory 内先写 staging 三件套并完成自检；
   - overwrite 时先把旧 manifest 原子移出提交位置，再依次备份旧 CSV；发布两个新 CSV 后，最后原子发布新 manifest 作为 commit marker；
   - 失败时恢复全部备份、删除无旧版本的新文件并清理 staging/backup；无旧产物和有旧三件套两种失败路径都必须测试。

`--validate-only` 仍先针对用户传入的真实 `--output-dir` 执行 protected-root 和已有目标/overwrite 预检；它只跳过 staging 发布、备份和文件替换，不得借临时目录绕过目标安全校验。

实现约束：

- 只使用标准库、Pillow、NumPy 和现有项目模块；不新增依赖。
- 使用 `pathlib` 和路径层级判断，不用字符串 `startswith`。
- 使用 `csv`/`json`，不做手写 CSV 拼接。
- 保留简短注释解释时区归一化、bbox 语义和事务发布。
- 原始 dataset 只读。

### U3. Focused tests and production-path compatibility

**Add** `tests/test_real_inspection_pilot_preparation.py`

所有测试数据和输出都位于 `tmp_path`，不得依赖网络、GPU、真实数据、本机视频、Web server 或未跟踪目录。

测试分组：

**Happy path and determinism**

- 合法两轮单 sequence 生成三个产物；
- 打乱 metadata 行顺序后 observation/frame 记录与 inspection mapping 稳定；
- 带不同时区 offset 的等价时刻正确归一化；
- mileage 0.1 米四舍五入和 1000 米进位正确；
- manifest hashes、计数、mapping、`path_base` 和 readiness 正确。

**Geometry and eligibility**

- 非空 mask 的 area、半开 bbox、center、bbox width/height、canvas width/height 正确；
- 空 mask 留在 observation records、从 frame records 排除；
- 某轮全部为空或只有单轮时 `inference_ready=false`；
- image/mask 尺寸不一致明确失败。

**Invalid input and isolation**

- 缺目录/文件/列/值、空表、非法后缀、非法 ID、非法时间、负或非有限里程、非法 clock、负 frame/ring 明确失败；
- duplicate composite key、同帧元数据冲突、disease type 冲突、inspection 时间重叠/并列明确失败；
- 多 sequence 输入明确失败；
- image/mask 路径穿越或放错子目录明确失败；
- output path 指向 dataset root、`data/simulated/`、正式 `outputs/` 或输入文件明确失败。

**Output safety**

- `--validate-only` 执行完整读取/几何/schema dry-run，并对真实 output path 做安全与覆盖预检，但不发布三个产物；
- 已有目标在无 overwrite 时失败；
- overwrite 只替换三件套，不删除同目录其他文件；
- 模拟发布中途失败时旧三件套完整恢复，新目标不残留半套；覆盖有旧三件套和首次发布两种情况，且旧 manifest 在新 CSV 发布前已移出 commit-marker 位置；
- CSV/JSON 不含绝对路径、用户名、`global_disease_id`；所有原样输出的自由文本拒绝 CSV 公式前缀；
- 测试前后 `data/simulated/` 与正式 `outputs/` 快照不变。

**Existing pipeline compatibility**

- frame CSV 通过现有 schema validator；
- ready 两轮产物直接传给现有 `AssociationAgent` history-only 入口；
- 第一轮只建立 baseline，第二轮 association 的 `history_inspection_ids=I0001`；
- not-ready 产物必须由生产脚本内的 `require_inference_ready()` 拒绝，不调用 coordinator；
- 两个 sequence 通过两次独立 preparation/coordinator 调用，工作目录和 candidate provenance 互不包含对方 sequence。

---

## Sequencing

1. U2 先实现纯校验、时序归一化和几何构造函数。
2. 完成 staging artifact 自检与事务发布，再开放 CLI `main()`。
3. U3 先跑 focused failure-path tests，再跑 coordinator compatibility。
4. U1 根据最终 CLI 行为写合同文档，确保命令和字段不漂移。
5. 在当前工作区运行专项、全量 pytest 与 artifact validator；任何正式 artifact 变化都视为失败。
6. 候选实现提交后，在临时 detached worktree/隔离副本中运行会改写日志和 state 的 full pipeline；失败则回到当前工作区修复并重新验收，不覆盖用户现有 WIP。

---

## Risks and Mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| coordinator 不识别 sequence | 跨 sequence candidate 泄漏 | CLI 单 sequence 强制合同，多 sequence 独立调用 |
| 真实数据字段与 v1 不一致 | 首批数据接入需要映射 | 明确版本化合同；先拒绝歧义，不做猜测式兼容 |
| `kict_*` 命名误导 | 被误认为 KICT 来源 | manifest/docs 明确 compatibility projection，保留真实 source 字段 |
| 空 mask 进入 candidate | 形成无效匹配 | observation audit 保留，frame projection 排除，逐轮 readiness gate |
| 覆盖中断产生混合产物 | manifest 与 CSV 不一致 | staging + backup + manifest-last + rollback tests |
| 路径泄漏或污染正式目录 | 开源/验收不可信 | dataset-relative POSIX 路径、protected roots、artifact isolation tests |
| fixture 测试被误述为真实验证 | 研究结论夸大 | 文档固定 claim boundary，完全不实现真实指标 |

---

## Verification Commands

```powershell
python -m py_compile scripts/prepare_real_inspection_pilot.py
python -m pytest tests/test_real_inspection_pilot_preparation.py -q -p no:cacheprovider
python scripts/validate_artifacts.py --project-root .
python -m pytest -q -p no:cacheprovider
```

提交候选实现后，在临时 detached worktree 中运行：

```powershell
python run.py --mode full_pipeline
python scripts/validate_artifacts.py --project-root .
```

临时 worktree 只用于验收，完成后安全移除；不得把其中生成的日志、state 或 artifacts 复制回当前脏工作区。

另外检查：

```powershell
git diff -- config/dag.yaml run.py orchestrator/agents/association_agent.py orchestrator/agents/memory_agent.py
git status --short
```

不得把已有 Web WIP、日志/state、本地视频目录或生成的 algorithm/video artifacts 纳入本次提交。

---

## Acceptance Criteria

- 只新增 data contract 文档、preparation CLI 和专项测试；不修改核心模块。
- 所有 requirements 中的 happy path、invalid path、transaction 和 compatibility 场景通过。
- preparation 输出满足现有 frame schema，且不含 GT 或本机绝对路径。
- history-only smoke 保持 baseline first、history-only query 和 no-id 边界。
- `data/simulated/`、正式 `outputs/`、主 pipeline、Web 和视频产物未被适配器或测试修改。
- focused tests、artifact validator、全量 pytest 和 full pipeline 验收通过。
- 独立 code review 无 P0/P1/P2；如发现问题，按 repair loop 修复并重新执行相关验收。

---

## Rollback

本阶段为纯新增能力。回滚时只移除：

- `scripts/prepare_real_inspection_pilot.py`
- `tests/test_real_inspection_pilot_preparation.py`
- `docs/real_inspection_input_foundation.md`

不需要迁移或恢复任何正式数据、DAG、Web 或模型文件。

---

## Follow-up

真实双轮样本与已审计 GT 到位后，另开 Goal 实现 post-inference evaluator。该后续阶段才允许产生真实 Association metrics，并继续禁止把像素面积差解释成纵向 Growth。
