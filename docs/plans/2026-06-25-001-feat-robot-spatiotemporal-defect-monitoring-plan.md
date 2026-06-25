---
title: Robot Spatiotemporal Defect Monitoring Plan
type: feat
status: active
date: 2026-06-25
origin: docs/brainstorms/2026-06-25-robot-spatiotemporal-defect-monitoring-requirements.md
---

# Robot Spatiotemporal Defect Monitoring Plan

## Summary

在现有单帧隧道病害检测和工程报告能力之上，新增机器人巡检序列的时空监测层。第一版消费现有 per-frame inspection report，完成序列排序、observation 抽取、defect track 聚合、图像变化证据判断，并导出 route-level robot inspection report。

---

## Problem Frame

当前项目已经能回答单张图的检测结果、mask 质量、不确定性、形态特征和工程位置摘要，但机器人巡检场景需要回答线路级问题：同一病害是否在一次巡检的连续帧中反复出现，位置证据是否稳定，图像证据是否出现明显变化，哪些点需要优先复核。

本计划覆盖 brainstorm 的 MVP 范围：report-level 时空轨迹算法层。真实机器人 middleware、跨巡检周期历史合并、SLAM/LiDAR、metric 3D fusion、深度时序预测和现场级变形验证全部后置；当前阶段只承诺可测试、可解释的图像序列证据。

---

## Requirements

**Sequence and report contract**

- R1. 系统能读取 robot sequence manifest，并支持 `frame_id`、image/report path、timestamp、mileage、ring id、camera id、`inspection_run_id`，以及可选 pose/calibration/depth。
- R2. 系统能复用现有 per-frame inspection report，不重复实现模型推理，也不丢失 selected mask、uncertainty、disagreement、morphology、review priority 和 spatial summary。
- R3. 系统能按 mileage、ring id、timestamp 或 manifest order 形成 route order，并记录 ordering confidence、location confidence 降级原因与 limitations。

**Tracking and trend**

- R4. 系统能把 observations 聚合为 persistent defect tracks，并输出 track confidence 与 association reasons。
- R5. 系统能计算 area delta、skeleton length delta、centroid shift、bbox change、class stability、uncertainty trend 和 risk trend，并标注这些变化是否在同一次巡检内可比。
- R6. 系统能给出 baseline-only、stable、apparent-change-evidence、suspected-growth、suspected-image-shape-change、uncertain 等 explainable trend labels；`suspected-growth` 只允许用于跨巡检周期且 comparable 的证据。
- R7. 系统能生成 review queue，优先呈现高不确定性、疑似增长、图像形态变化证据、risk rising 或定位置信不足的 tracks。

**Engineering and safety**

- R8. 系统输出必须保留 engineering location 字段，包括 mileage、ring id、clock position、normalized center、local 3D status、source、accuracy level 和 limitations。
- R9. 缺失 metadata 时系统不能崩溃，应输出 partial/uncertain status 和 limitation。
- R10. 系统不能在缺少标定和人工复核时宣称厘米级定位或真实结构变形量。
- R11. track history 结构要能支持未来 sequence prediction，但第一版只实现规则化图像证据；跨巡检周期预测和真实增长验证不属于 Phase 1。

---

## Key Technical Decisions

- KTD1. **从 per-frame report 开始。** `inspection_report.py` 已经有 assessment、evidence、views、multidomain results 和 spatial summary；新层先消费这些 JSON，而不是改动模型推理入口。
- KTD2. **新增 defect track 作为核心抽象。** track 把多帧 observations 变成一个工程对象，包含位置、趋势、置信度、复核原因和限制说明。
- KTD3. **第一版使用保守规则匹配。** 用 class、mileage/ring proximity、normalized center distance、bbox/morphology similarity 和时间连续性做 association；同时加入 comparability gate，跨 camera、视角未知或 pose/calibration/depth 缺失时降低 track/trend confidence。
- KTD4. **趋势标签必须可解释且防过度承诺。** 同一次巡检内的面积、bbox 或骨架变化只能称为 apparent-change-evidence；`suspected-growth` 必须来自跨巡检周期且 comparable 的证据，`suspected-image-shape-change` 只表示 image-evidence cue，不表示已验证结构变形。
- KTD5. **算法层先于 Web 层。** 先稳定 JSON contract、fixtures 和 tests，再把 route-level tracks 接入 Web 展示，避免 UI 先行导致算法证据不稳。

---

## High-Level Technical Design

```mermaid
flowchart TB
  A[Robot sequence manifest] --> B[Frame ordering]
  B --> C[Per-frame inspection reports]
  C --> D[Observation extraction]
  D --> E[Defect track association]
  E --> F[Trend metrics and labels]
  F --> G[Review queue]
  F --> H[Robot inspection report]
  H --> I[Web and presentation artifacts]
```

新模块只依赖现有 report contract，不直接改 SegFormer 推理。这样可以先用 fixture reports 写测试，后续再接真实机器人图片序列。

---

## Delivery Phases

- Phase 1. Algorithm and report core: 完成 U1-U5，包括 manifest、FrameRecord、observation schema、track association、comparability gate、trend labels、route-level report 和 tests。
- Phase 2. Demo integration: 在 U5 已输出 fixture-backed route report 后执行 U6，只消费静态或生成的 route report JSON，不改变现有单图拖拽检测。
- Phase 3. Research and competition packaging: 在 Phase 1 有可复现实验/fixture 输出后执行 U7，再更新比赛、专利和 PPT 材料。

---

## Implementation Units

### U1. Robot sequence input contract

- **Goal:** 定义并解析 robot sequence manifest，形成稳定的 `FrameRecord` 列表和 frame order。
- **Files:** `robot_sequence.py`, `tests/test_robot_sequence.py`
- **Patterns:** 参考现有报告输出的轻量 JSON contract 风格，保持字段简单、可人工编辑。`FrameRecord` 至少包含 `frame_id`、`manifest_index`、`image_path`、`report_path`、`timestamp`、`mileage`、`ring_id`、`camera_id`、`inspection_run_id`、可选 pose/calibration/depth、ordering confidence 和 limitations。首版只解析、校验、透传 pose/calibration/depth，不做 SLAM、LiDAR、metric 3D fusion、厘米级定位或真实结构变形计算。
- **Test scenarios:**
  - `frame_id` 是稳定字段，重复 `frame_id` 被拒绝或降级为 invalid manifest。
  - mileage 和 timestamp 完整时按 route order 排序。
  - manifest 无序输入时输出稳定顺序。
  - mileage 缺失时使用 timestamp/manifest order，并记录 ordering limitation 与 location confidence 降级。
  - timestamp 缺失时不崩溃，ordering confidence 降级。
- **Acceptance:** 满足 R1、R3、R9。

### U2. Observation extraction from per-frame reports

- **Goal:** 从现有 single-image inspection report 抽取 sequence observation。
- **Files:** `spatiotemporal_monitoring.py`, `tests/test_spatiotemporal_monitoring.py`
- **Patterns:** 复用 `inspection_report.py` 的 report 字段和 `spatial_mapping.py` 的 spatial summary 语义。首版 observation schema 至少包含 `observation_id`、`frame_id`、`inspection_run_id`、`class_id`/`class_name`、geometry/bbox、area、skeleton_length、location、confidence、status、measurement_basis、claim_level 和 limitations。metadata precedence 为：manifest 是 route-level source of truth；report.spatial_summary 补充 geometry/location evidence；冲突进入 limitations。多病害场景第一版先按 selected foreground / main class 抽取 single foreground observation，并显式记录 multi-component limitation；后续再扩展 class + connected component。
- **Test scenarios:**
  - report 含 `spatial_summary` 时 observation 有 location、area、bbox 和 confidence。
  - report 缺少 foreground/location 时 observation 标记为 partial。
  - uncertainty、disagreement、review priority、morphology 字段被保留。
  - manifest metadata 与 report metadata 冲突时，以 manifest 作为 route-level source of truth，并记录 limitation。
  - 多 component mask 在第一版降级为 single foreground observation，并记录 limitation。
- **Acceptance:** 满足 R2、R8、R9。

### U3. Defect track association

- **Goal:** 将 observations 聚合成 persistent defect tracks。
- **Files:** `spatiotemporal_monitoring.py`, `tests/test_spatiotemporal_monitoring.py`
- **Patterns:** 保守匹配，优先避免误合并；association reasons 需要能被报告展示。
- **Test scenarios:**
  - class 相同、mileage/ring 接近、normalized center 接近时归为一个 track。
  - class 不同或位置差距大时分成多个 tracks。
  - 缺少 location 时 track confidence 降级，不输出高置信合并。
  - `camera_id` 不同、视角未知或 pose/calibration/depth 不可比时，只能弱关联或拆分 track，并记录 comparability limitation。
  - 同一序列多帧输入时 track id 稳定且 observations 顺序正确。
  - track 输出包含 ordered observation history，history 中每条 observation 能通过 `frame_id` 回指原始帧和 evidence link。
- **Acceptance:** 满足 R4、R9、R10。

### U4. Growth and deformation metrics

- **Goal:** 对每个 track 计算可解释 trend metrics 和 trend labels。
- **Files:** `spatiotemporal_monitoring.py`, `tests/test_spatiotemporal_monitoring.py`
- **Patterns:** 规则阈值集中定义，避免魔法数字散落；所有 label 都给出 reason、measurement_basis、claim_level 和 comparability_status。`claim_level` 至少区分 `rule_evidence_only`、`not_prediction`、`not_field_verified`、`requires_manual_review`。同一次巡检内的变化默认是 apparent-change-evidence 或 uncertain；只有跨巡检周期、metadata comparable 或 manual verified 的观测才能输出 suspected-growth。`suspected-image-shape-change` 表示 image-evidence cue，不表示已验证结构变形。
- **Test scenarios:**
  - 单 observation 输出 baseline-only。
  - 同一 `inspection_run_id` 内 area 或 skeleton length 明显增加时不得输出 suspected-growth，只能输出 apparent-change-evidence 或 uncertain。
  - comparable cross-cycle observation 的 area 或 skeleton length 明显增加时可以输出 suspected-growth。
  - centroid shift 或 bbox shape 明显变化但 metadata 不可比时输出 uncertain 或 apparent-change-evidence，不输出已验证结构变形。
  - 指标缺失或 uncertainty 过高时输出 uncertain。
  - 小幅变化低于阈值时输出 stable。
- **Acceptance:** 满足 R5、R6、R10、R11。

### U5. Route-level robot inspection report

- **Goal:** 导出工程可读的 route-level report，包括 tracks、observations、trend、review queue 和 limitations。
- **Files:** `robot_inspection_report.py`, `tests/test_robot_inspection_report.py`
- **Patterns:** 延续 `inspection_report.py` 的 JSON-first 输出方式，字段命名保持直观。
- **Test scenarios:**
  - report 包含 route summary、frame count、track count、review count。
  - review queue 将 suspected-growth、risk rising、high-uncertainty、low-location-confidence track 排在 stable track 前。
  - latest location 保留 mileage、ring id、clock position、normalized center、local 3D status、source、accuracy level 和 limitations。
  - 每个 track/trend 都包含 claim_level、measurement_basis、comparability_status 和 limitations，供 Web/PPT 防止过度承诺。
  - 缺少 metadata 时 route report 汇总 limitations。
- **Acceptance:** 满足 R7、R8、R9、R10。

### U6. Demo integration and sample artifact

- **Goal:** 让 Web/PPT 可以展示 route-level 结果，但不影响现有拖图单帧检测。
- **Files:** `web_demo/index.html`, `web_app.py`, `docs/presentations/tunnel-defect-project/index.html`
- **Patterns:** 只在 U5 已输出 fixture-backed route report 且 report contract 稳定后执行。先加载静态 route report JSON 做展示；确认 contract 稳定后再考虑多图上传。建议 fixtures 路径为 `tests/fixtures/robot_sequence/manifest.json`、`tests/fixtures/robot_sequence/frame_reports/*.json`、`tests/fixtures/robot_sequence/expected_route_report.json`；Web 示例可消费 `web_demo/assets/robot_route_report.json` 或后续 `/api/robot-route-report`。
- **Test scenarios:**
  - 现有单图拖拽检测仍可使用。
  - 示例 route report 能展示 track 趋势、位置、review reason 和 evidence link。
  - Web 展示 claim_level、measurement_basis、comparability_status 和 limitations，避免把 rule evidence 讲成真实预测或现场验证。
  - Web 文案区分 model evidence、engineering location 和 limitations。
- **Acceptance:** 满足 R2、R7、R8。

### U7. Research, patent, and competition documentation

- **Goal:** 把算法创新表达成可用于老师展示、比赛方案和专利交底的材料。
- **Files:** `docs/competition/robot-spatiotemporal-monitoring.md`, `docs/competition/cs-202613-requirements-matrix.md`, `docs/presentations/tunnel-defect-project/index.html`
- **Patterns:** 明确区分已实现能力、规则化趋势证据、未来预测能力，避免把 prototype 说成现场验证系统。
- **Test scenarios:**
  - 文档解释“机器人图像序列 -> 病害轨迹 -> 趋势证据 -> 工程报告”的流程。
  - 文档包含一条核心创新 claim、已实现/未实现能力表、专利交底候选创新点、比赛展示故事线，以及至少一个 route report 证据样例摘要。
  - 文档包含 limitations，不声称无依据的厘米级定位或真实结构变形。
  - PPT/比赛材料能用通俗语言讲清创新点。
- **Acceptance:** 满足 R10、R11。

---

## Scope Boundaries

**Phase 1 in scope**

- Manifest/parser、observation extraction、track association、trend metrics、route-level report、review queue。

**Deferred for later**

- 基础 Web/文档展示。
- 机器人 middleware 接入。
- 跨巡检周期历史合并和长期 track persistence。
- 多周期真实巡检数据集。
- SLAM/LiDAR/metric 3D fusion。
- 学习型 sequence prediction。
- 现场级变形监测验证。

**Out of scope**

- 机器人运动控制。
- 自动维修决策。
- 结构安全认证。
- 无标定数据的厘米级定位承诺。

---

## System-Wide Impact

- 新增模块应位于现有单帧 pipeline 上层，不改变 SegFormer 训练、推理和已有 Web drag-and-drop 行为。
- 新 JSON contract 会成为后续 Web、PPT、比赛材料、专利交底的共同数据源。
- route-level JSON 必须在数据层携带 claim_level、measurement_basis、comparability_status 和 limitations，不能只依赖展示文案提醒风险。
- 缺失 metadata 的处理会影响所有工程化表述，必须在 report 和 Web 中保持一致。

---

## Risks And Dependencies

| Risk | Impact | Mitigation |
| --- | --- | --- |
| 缺少真实多周期机器人数据 | 不能证明真实长期预测效果 | Phase 1 只承诺同次巡检内图像证据和 baseline；suspected-growth 需要 comparable cross-cycle 或 manual verified evidence |
| metadata 不完整 | 位置和趋势可信度下降 | 使用 source、accuracy level、limitations 和 partial/uncertain status |
| 误把不同病害合成一个 track | 造成错误增长判断 | 保守 association，证据不足时新建 uncertain track |
| Web 范围膨胀 | 延误算法核心 | U6 延后到 U5 产出 fixture-backed route report 后，Web 只展示稳定 contract |
| 专利/比赛表述过度 | 影响可信度 | route report 数据层携带 claim guard，文档明确 prototype 边界和未验证能力 |

---

## Acceptance Examples

- AE1. 给定含 `frame_id`、timestamp 和 mileage 的 manifest，U1 输出稳定 route order，并记录 ordering confidence。
- AE2. 给定两个位置接近的同类 observations，U3 输出一个 track，U4 输出 area/skeleton deltas。
- AE3. 给定缺少 mileage 的 sequence，U1/U5 不崩溃，并在 route report 中显示 ordering/location confidence 降级和 limitation。
- AE4. 给定同一次巡检内 track 面积和骨架长度明显增加，U4 不输出 suspected-growth，而是输出 apparent-change-evidence 或 uncertain，并要求人工复核。
- AE5. 给定只有一个 observation 的 track，U4 输出 baseline-only，而不是增长预测。

---

## Verification

Focused tests after Phase 1 implementation:

```powershell
python -m pytest --rootdir . tests/test_robot_sequence.py tests/test_spatiotemporal_monitoring.py tests/test_robot_inspection_report.py -q -o cache_dir=.pytest_cache
```

Regression tests for existing report and spatial behavior:

```powershell
python -m pytest --rootdir . tests/test_inspection_report.py tests/test_run_confidence_risk_spatial_mapping.py -q -o cache_dir=.pytest_cache
```

Web integration tests after U6:

```powershell
python -m pytest --rootdir . tests/test_web_app.py -q -o cache_dir=.pytest_cache
```

Fixture contract for U6:

```text
tests/fixtures/robot_sequence/manifest.json
tests/fixtures/robot_sequence/frame_reports/*.json
tests/fixtures/robot_sequence/expected_route_report.json
web_demo/assets/robot_route_report.json
```

---

## Documentation And Operational Notes

- `docs/competition/robot-spatiotemporal-monitoring.md` 应作为比赛/专利方向说明的主文档，但只在 Phase 1 有 route report 证据后写入完整叙事。
- `docs/presentations/tunnel-defect-project/index.html` 只在有实际 route report 示例后更新，避免 PPT 展示先于算法证据。
- 所有 docs 中的路径保持 repo-relative，方便 Gitee 开源展示。

---

## Sources And Research

- `spatial_mapping.py` 已提供 location source、accuracy level、limitations、mileage、ring id、clock position 等工程位置基础。
- `inspection_report.py` 已提供 per-frame structured report，可作为 U2 的输入 contract。
- `docs/brainstorms/2026-06-25-robot-spatiotemporal-defect-monitoring-requirements.md` 是本计划的需求来源。
- `docs/plans/2026-06-20-001-feat-spatial-mapping-prototype-plan.md` 是空间定位能力的上游计划。
