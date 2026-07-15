---
date: 2026-07-16
topic: real-inspection-input-foundation
status: proposed
---

# TunnelDefect Real Inspection Input Foundation Requirements

## Summary

下一阶段先实现一个最小真实巡检输入适配基础层：每次把一个 sequence 的图像、mask 和工程元数据转换为现有 history-only no-id Association 可直接消费的中性记录。该阶段只验证数据合同、时序归一化、mask 几何提取和链路兼容性，不做 GT 评分，不声称已经完成真实跨巡检验证。

---

## First-Principles Decision

当前最需要解决的问题是“真实数据能否以不泄漏答案、不污染正式产物的方式进入现有时序链路”，而不是继续增加页面、Agent 或匹配算法。完整真实双轮 Pilot 还依赖尚未提供的真实配对样本和人工 Ground Truth；现在直接实现评估层，会得到只能靠 synthetic fixture 自证的空壳。

因此本阶段选择可独立验收的 U1 基础能力：

1. 锁定最小真实巡检数据合同；
2. 从已有 mask 提取可审计几何量；
3. 按真实时间顺序生成 sequence 内的 Association inspection ID；
4. 投影为现有 schema 兼容记录；
5. 用临时夹具证明可进入现有 history-only coordinator；
6. 明确把 GT、真实指标和纵向 Growth 留给后续独立 Goal。

---

## Candidate Directions

| Direction | Immediate value | Blocking risk | Decision |
|---|---|---|---|
| 最小真实巡检输入适配基础层 | 可在没有真实数据包时用临时夹具完整验收，并直接为后续 Pilot 铺路 | 只解决入口，不产生真实算法指标 | **本阶段实施** |
| 完整真实 Association evaluator | 能形成真实 top-1、拒识和错误案例 | 缺少真实双轮样本与已审计 GT，无法完成真实验收 | 后续数据就绪后实施 |
| 视觉 embedding / 新匹配策略 | 可能改善困难候选匹配 | 没有真实 GT 时容易继续拟合 synthetic fixture | 暂缓 |
| Web 上传与异步任务 | 展示和部署更方便 | 不增加研究证据，扩大维护范围 | 暂缓 |

---

## Core Flow

```text
images/ + masks/ + metadata.csv
                |
                v
    contract and path validation
                |
                v
    timestamp-based ID normalization
                |
                v
      mask geometry extraction
                |
       +--------+---------+
       |                  |
       v                  v
observation_records   frame_records
(all observations)    (eligible only)
                           |
                           v
              existing history-only coordinator
                    (compatibility test only)
```

---

## Requirements

### R1. One preparation entry point

- 提供一个 CLI：`scripts/prepare_real_inspection_pilot.py`。
- 支持 `--dataset-root`、`--output-dir`、`--validate-only` 和 `--overwrite`。
- 每次调用只允许 metadata 中存在一个唯一 `sequence_id`；多个 sequence 必须分别调用并写入不同 output directory，禁止合并后直接交给 sequence-blind history-only coordinator。
- 不建设 registry、插件系统、Adapter 抽象基类或第二套编排框架。

### R2. Raw input contract

- `dataset-root` 下必须存在 `images/`、`masks/` 和 `metadata.csv`。
- `metadata.csv` 每行代表一个局部病害观测，至少包含：
  - `sequence_id`
  - `source_inspection_id`
  - `frame_id`
  - `timestamp`
  - `mileage_m`
  - `ring_id`
  - `clock_direction`
  - `image_file`
  - `mask_file`
  - `local_observation_id`
  - `disease_type`
- 图像和 mask 支持 `.jpg`、`.jpeg`、`.png`、`.bmp`。
- 输入路径使用相对于 dataset root 的路径；`image_file` 必须解析到 `images/` 内，`mask_file` 必须解析到 `masks/` 内。禁止路径穿越，且本阶段要求文件真实存在。
- 图像与对应 mask 的像素尺寸必须一致，避免 bbox 和中心点落在错误坐标系。
- `sequence_id`、`source_inspection_id`、`local_observation_id` 只允许 `[A-Za-z0-9._-]+`；禁止 `::`、控制字符和电子表格公式前缀。`frame_id` 与 `ring_id` 必须是非负整数。
- `timestamp` 必须是带 `Z` 或 UTC offset 的 ISO-8601 时间；统一转成 UTC 后排序和输出为 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`，不接受无时区时间，也不允许混用隐式本地时区。
- `mileage_m` 必须是有限非负数；`clock_direction` 和 `disease_type` 必须是非空文本。所有会原样写入 CSV 的自由文本拒绝 `= + - @` 公式前缀。
- `clock_direction` 只接受 `1点` 至 `12点` 的规范值；同帧时间一致性按归一化后的 UTC 时刻判断。

### R3. Validation and uniqueness

- metadata 不能为空，必需字段逐行不能为空。
- 复合键 `(sequence_id, source_inspection_id, frame_id, local_observation_id)` 必须唯一。
- 同一 `(sequence_id, source_inspection_id, frame_id)` 的 `timestamp`、`mileage_m`、`ring_id`、`clock_direction` 和 `image_file` 必须一致；同一 inspection 内重复出现的 `local_observation_id` 必须保持相同 `disease_type`。
- 同一 sequence 内，每个 source inspection 的时间区间必须能严格排序；时间区间并列或重叠时拒绝运行。
- 每个 sequence 至少需要两个 source inspections；单轮样本只能通过基础字段检查，但不得标为 inference-ready。
- 错误必须包含文件、行号、字段或复合键，不得静默跳过。

### R4. Stable inspection normalization

- 在每个 `sequence_id` 内按 inspection 最早 timestamp 生成 `association_inspection_id=I0001, I0002, ...`。
- 输出同时保留 `source_inspection_id` 与 `association_inspection_id`。
- 归一化结果不能依赖 metadata 输入行顺序或 source inspection 命名格式。
- 不同 sequence 独立编号，禁止共享 candidate history。

### R5. Mask geometry and empty-mask boundary

- mask 非零像素视为病害区域，提取面积、bbox、中心点、mask 宽高。
- 空 mask 不崩溃，保留在 `observation_records.csv`，并写入：
  - `association_eligible=false`
  - `exclusion_reason=empty_mask`
- 空 mask 不得进入 `frame_records.csv` 或 history-only candidate/query。
- 若某个 inspection 过滤后没有有效观测，manifest 必须把对应 sequence 标记为 `inference_ready=false` 并写明原因。
- bbox 沿用现有 KICT 兼容的半开区间：`x2=max_x+1`、`y2=max_y+1`；中心为 `(x1+x2-1)/2`、`(y1+y2-1)/2`，保留两位小数。
- 兼容字段 `kict_mask_width=x2-x1`、`kict_mask_height=y2-y1` 表示前景 bbox 尺寸；中性字段 `mask_canvas_width/height` 单独记录 mask 画布尺寸。

### R6. Neutral outputs

CLI 只生成三个产物：

- `observation_records.csv`
- `frame_records.csv`
- `preparation_manifest.json`

`observation_records.csv` 的最小字段为：

- provenance/identity：`sequence_id`、`source_inspection_id`、`association_inspection_id`、`frame_id`、`local_observation_id`、`image_path`、`mask_path`
- engineering metadata：`timestamp`、`mileage_m`、`mileage_text`、`ring_id`、`clock_direction`、`disease_type`
- neutral geometry：`area_px`、`bbox_x1/y1/x2/y2`、`center_x/y`、`bbox_width/height`、`mask_canvas_width/height`
- boundary fields：`association_eligible`、`exclusion_reason`、`observation_source`、`comparability_status`、`data_contract_version`

空 mask 固定写 `area_px=0`、bbox/center=`-1`、`bbox_width/height=0`，同时保留真实 `mask_canvas_width/height`。

固定字段语义：

- `data_contract_version=real_inspection_pilot_v1`
- `observation_source=real_inspection_mask_input`
- `comparability_status=not_longitudinally_comparable`

兼容投影同时固定：

- `inspection_id=association_inspection_id`
- `frame_id` 和 `ring_id` 使用校验后的十进制整数字符串
- `mileage_text` 先把 `mileage_m` 按 decimal half-up 取到 0.1 米，再执行千米进位并生成 `K{km}+{meter:05.1f}`，例如 `K12+006.0`；不得生成 `K12+1000.0`
- 非空 mask 行写 `has_crack=true`、`kict_area_px>0`、完整半开 bbox/center，以及表示前景 bbox 尺寸的 `kict_mask_width/height`
- `kict_image_path`、`kict_mask_path` 使用 dataset-root-relative POSIX 路径

本阶段不接受或输出 `global_disease_id`，也不读取 GT、split 或审核状态。

### R7. Compatibility projection

- `frame_records.csv` 必须满足现有 `REQUIRED_SCHEMAS["robot_kict_frame_records"]`。
- 允许额外保留 `sequence_id`、`source_inspection_id`、`association_inspection_id`、`local_observation_id`、`data_contract_version`、`kict_mask_width` 和 `kict_mask_height`。
- `image_id=<sequence_id>::<association_inspection_id>::<frame_id>`。
- `disease_id=<sequence_id>::<association_inspection_id>::<local_observation_id>`，它只表示局部观测，不表示跨轮同一病害。
- `kict_*` 字段仅作为现有 schema 的兼容命名；manifest 和文档必须明确来源是真实巡检输入适配，不是 KICT。
- 每次产物只包含一个 sequence，因此测试可直接调用现有 `run_history_only_association()`；not-ready 产物必须被 compatibility gate 拒绝，不得进入 coordinator。不得复制 coordinator 或增加生产 runner。

### R8. Safe and portable artifacts

- `--validate-only` 执行完整 dry-run，包括打开图像/mask、尺寸校验、mask 几何计算、投影构造和现有 frame schema 校验，但不写任何产物。
- 默认拒绝覆盖已有的三个目标产物；`--overwrite` 只能替换本 CLI 自己的三个文件，不得递归删除目录。
- 解析后的 `output-dir` 必须与 dataset root 分离，并拒绝落入项目的 `data/simulated/`、正式 `outputs/`，或与任一输入文件重合；路径等价形式必须得到相同判断。
- CSV 和 manifest 内路径统一写成 dataset-root-relative POSIX 路径，不写用户名或本机绝对路径。
- manifest 写入 `path_base=dataset_root`，并记录 contract version、生成时间、metadata SHA-256、所有被引用 image/mask 的相对路径、文件大小与 SHA-256、两个 CSV 的 SHA-256、inspection ID 映射、每轮原始/有效/排除计数、sequence readiness 和原因；manifest 不记录自身哈希。
- 三个产物先写入 output directory 内的临时文件并完成自检。覆盖模式先备份已有三件套，再替换两个 CSV，最后发布 manifest 作为完整性提交标志；任一步失败必须恢复全部备份、移除未对应旧文件的新产物并清理临时文件。
- 不修改原始 dataset，不写 `data/simulated/`、正式 `outputs/` 或现有 benchmark。

### R9. Claim boundary

- 本阶段只证明“输入合同和现有链路兼容”。
- 没有真实数据包时，只能称为 fixture-backed contract validation。
- 不计算真实 Association accuracy，不输出 GT error cases。
- 不输出增长、减小、稳定或长期演化结论。
- 不声称支持生产级真实机器人在线巡检。

---

## Acceptance Examples

- **AE1:** 两轮乱序 metadata 仍按 timestamp 稳定映射为 `I0001/I0002`，I0001 只建 baseline，I0002 只能看到 I0001。
- **AE2:** metadata 同时包含两个 sequence 时 CLI 明确拒绝；两个 sequence 分别调用且使用相同 source inspection、frame 和 local observation ID 时，各自产物与 coordinator 工作目录完全隔离。
- **AE3:** 空 mask 出现在 observation audit，但不出现在 frame records；某轮全部为空时该 sequence 明确为 not ready。
- **AE4:** `../outside.png`、图像/mask 尺寸不一致、重复复合键、时间区间重叠和负里程均产生可定位错误。
- **AE5:** `--validate-only` 和全部测试运行前后，`data/simulated/` 与正式 `outputs/` 哈希不变。
- **AE6:** 所有派生 ID、CSV 与 manifest 均不含 `global_disease_id` 或本机绝对路径。
- **AE7:** 写第二个临时产物时模拟失败，不发布半套新产物；已有完整产物在失败后保持不变。
- **AE8:** 单像素前景 `(x=3,y=4)` 输出半开 bbox `(3,4,4,5)`、中心 `(3,4)`、`kict_mask_width/height=1`，同时保留真实画布尺寸。
- **AE9:** 带不同时区 offset 但表示同一时刻的同帧记录通过一致性检查；无时区 timestamp、非法方位、`images/`/`masks/` 外路径均失败。

---

## Non-Goals

- 不实现真实 Association evaluator、GT audit、split、headline metrics 或错误案例评分；
- 不接入新模型、不训练、不下载权重、不做模型推理；
- 不修改 AssociationAgent 评分公式、Memory、Growth、DAG 或主 pipeline；
- 不修改 Web、视频链路、前后端架构或部署方式；
- 不引入数据库、LLM、Agent hierarchy、Hungarian、tracker 或 embedding；
- 不下载、复制或提交真实巡检数据。

---

## Completion Gate

- 单一 preparation CLI 与专项测试全部通过。
- 临时双轮夹具能生成三个隔离产物，并通过现有 frame schema 校验。
- 单 sequence 兼容性测试能直接调用现有 history-only coordinator，且没有当前/未来 candidate 泄漏；多 sequence 输入在 preparation 阶段被拒绝，独立调用的产物互不污染。
- 全量 pytest 与现有 artifact validator 通过。
- 文档明确：完成的是输入适配基础层，不是完整真实 Pilot，也不是实际算法效果验证。

---

## Follow-up Goal

真实双轮样本和经人工审核的 GT 到位后，再单独实施 `Real Inspection Association Evaluation`：GT 后连接、`weighted_no_id` 与 `spatial_only` 对照、指标分母、错误案例和真实 Pilot 报告。该后续 Goal 不应提前混入本阶段。

---

## Sources

- `docs/brainstorms/2026-07-15-real-inspection-association-pilot-requirements.md`
- `docs/plans/2026-07-15-002-feat-real-inspection-association-pilot-plan.md`
- `orchestrator/history_only_association.py`
- `orchestrator/schema.py`
- `tests/test_history_only_association.py`
