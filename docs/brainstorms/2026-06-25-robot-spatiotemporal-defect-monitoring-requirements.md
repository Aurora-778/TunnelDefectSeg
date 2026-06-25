---
title: Robot Spatiotemporal Defect Monitoring Requirements
topic: robot-spatiotemporal-defect-monitoring
date: 2026-06-25
---

# Robot Spatiotemporal Defect Monitoring Requirements

## Summary

把当前项目从单张隧道病害图像分割，升级为面向机器人巡检的时空病害监测能力。机器人沿隧道连续拍摄图像后，系统需要完成单帧检测、疑点标记、工程位置描述、同一病害跨帧/跨周期关联，以及增长或变形趋势判断。

核心目标是让项目从“这张图里有没有病害”变成“这条巡检线路上哪些病害在哪里、是否在变化、哪些需要优先复核”。

---

## Problem Frame

目前项目已经具备较强的单帧基础：SegFormer 分割、selected mask、uncertainty/disagreement、morphology、review priority、spatial summary 和 inspection report 都已经存在。但真实机器人巡检不是只看一张图，而是一串按时间和里程连续产生的图片。

工程人员真正关心的是：同一个病害是否反复出现，位置在哪里，面积或骨架长度有没有变大，是否疑似变形，是否应该安排人工复核。只展示单张 mask 不能回答这些问题，所以需要在现有模型输出之上增加“时空聚合和病害轨迹”层。

---

## Key Decisions

- **先做 report-level 时空轨迹层。** 第一版不直接接机器人硬件或 SLAM，而是消费现有 per-frame inspection report。这样更容易落地、测试和展示，也能保留后续接机器人元数据的空间。
- **趋势判断先用可解释规则。** 在缺少真实多周期巡检数据前，不直接上深度时序预测模型。第一版用面积、骨架长度、中心点、bbox、风险和不确定性趋势做 explainable growth/deformation evidence。
- **所有定位和趋势都带来源与置信说明。** 缺少 mileage、ring id、pose、depth 或 calibration 时不能硬说精准定位，只能输出 partial/uncertain 结论和 limitations。
- **Web 是展示层，不是创新核心。** 创新应放在 robot image sequence 的时空聚合、疑点优先级、病害轨迹和趋势证据上，Web 负责把结果展示清楚。

---

## Actors

- A1. Robot inspection operator: 需要在一次机器人巡检后看到整条线路的缺陷、疑点和复核优先级。
- A2. Maintenance engineer: 需要看到工程语言描述，例如里程、环号、方位、面积变化、骨架长度变化、风险等级和建议复核原因。
- A3. Research and patent reviewer: 需要看到项目不是简单套用 SegFormer，而是把机器人图像序列转成可解释的时空监测证据。

---

## Key Flows

- F1. Robot sequence ingestion
  - **Trigger:** 机器人完成一次隧道巡检，产生一组图像或 per-frame reports。
  - **Steps:** 读取 image/report path、timestamp、mileage、ring id、camera id、可选 pose/calibration/depth；按时间和空间顺序整理。
  - **Outcome:** 形成一条可追踪的巡检序列，并记录缺失元数据带来的限制。

- F2. Per-frame inference and evidence preservation
  - **Trigger:** 序列中的某一帧需要检测。
  - **Steps:** 复用当前单帧检测输出，包括 selected mask、uncertainty、disagreement、morphology、review priority、spatial summary 和 inspection report。
  - **Outcome:** 每张图都有可回溯证据，后续轨迹层不覆盖原始判断依据。

- F3. Spatiotemporal aggregation
  - **Trigger:** 多帧检测结果完成后。
  - **Steps:** 按 defect class、mileage/ring proximity、normalized center、bbox/morphology similarity 和时间连续性，把可能属于同一物理病害的 observations 归到同一个 track。
  - **Outcome:** 输出 persistent defect tracks；证据不足时创建 uncertain/new track，避免误合并。

- F4. Growth and deformation monitoring
  - **Trigger:** 一个 defect track 内存在多次 observation。
  - **Steps:** 比较 area、skeleton length、centroid、bbox、risk、uncertainty 的变化。
  - **Outcome:** 给出 baseline-only、stable、suspected-growth、suspected-deformation 或 uncertain 标签。

- F5. Engineering report generation
  - **Trigger:** 一次序列分析完成。
  - **Steps:** 汇总 route summary、tracks、observations、review queue、latest location、limitations 和证据视图。
  - **Outcome:** 输出工程人员能读懂的线路级巡检报告。

---

## Requirements

**Sequence and evidence**

- R1. 系统必须定义 robot sequence 输入契约，支持 frame id、image/report path、timestamp、mileage、ring id、camera id，以及可选 pose/calibration/depth。
- R2. 系统必须保留现有单帧 evidence，包括 selected mask、uncertainty、disagreement、morphology、review priority 和 spatial summary。
- R3. 系统必须按 timestamp 和 route metadata 排序；mileage 缺失时可退化为 timestamp/manifest order，并降低位置可信度。

**Track and trend**

- R4. 系统必须把多帧 observations 聚合为 persistent defect tracks，并为每个 track 给出 track confidence 和 association reasons。
- R5. 系统必须计算 track-level temporal metrics，包括 area delta、skeleton length delta、centroid shift、bbox change、class stability、uncertainty trend 和 risk trend。
- R6. 系统必须输出 explainable trend labels：baseline-only、stable、suspected-growth、suspected-deformation、uncertain。
- R7. 系统必须生成 review queue，优先排列高不确定性、疑似增长、疑似变形、风险上升或定位置信不足的 track。

**Engineering expression and safety**

- R8. 系统必须用工程语言表达位置，包括 mileage、ring id、clock position、normalized center、local 3D status、source、accuracy level 和 limitations。
- R9. 系统必须在 metadata 缺失时继续运行，并明确输出 partial/uncertain status 与 limitations。
- R10. 系统不得在缺少 calibrated robot pose、depth、camera geometry 或人工复核时宣称厘米级定位或真实结构变形量。
- R11. 系统设计必须为未来 sequence prediction 留出 track history 字段，但第一版只承诺可解释趋势证据，不承诺深度预测模型效果。

---

## Acceptance Examples

- AE1. **Ordered robot run covers R1, R3.** 给定 10 个带 timestamp 和 mileage 的 frame reports，系统输出按路线顺序排列的 route report。
- AE2. **Same defect tracking covers R4, R5.** 给定两个 class 相同、位置接近、时间连续的 observations，系统把它们归入一个 defect track 并报告面积/骨架变化。
- AE3. **Missing mileage covers R3, R8, R9.** 给定没有 mileage 的序列，系统仍能按 timestamp 输出结果，但 location confidence 降级并显示 limitation。
- AE4. **Suspicious growth covers R5, R6, R7.** 给定同一 track 的 area 和 skeleton length 明显增加，系统标记 suspected-growth 并加入 review queue。
- AE5. **Baseline only covers R6, R10.** 给定只有一次 observation 的新病害，系统标记 baseline-only，而不是伪造增长预测。

---

## Scope Boundaries

**MVP scope**

- 新增 robot sequence manifest/loader。
- 从现有 per-frame inspection report 抽取 observation。
- 聚合 observations 为 defect tracks。
- 计算可解释的 growth/deformation indicators。
- 输出 route-level robot inspection report。
- 补充 tests 和 competition/patent 方向文档。

**Deferred for later**

- 真实机器人 middleware 接入。
- SLAM/LiDAR/三维点云融合。
- 多巡检周期真实数据集构建。
- 深度时序预测模型。
- 现场级变形监测验证。

**Outside this product's identity**

- 机器人运动控制。
- 结构安全自动判定。
- 无人工复核的维修决策。
- 无标定证据的厘米级定位承诺。

---

## Success Criteria

- 当前单帧 Web 和 report 能力不被破坏。
- 一组 per-frame reports 可以被组合成 route-level report。
- 至少有 synthetic/fixture tests 覆盖排序、缺失 metadata、track association、trend labels 和 review queue。
- 输出能清楚区分 model evidence、engineering location、trend conclusion 和 limitations。
- 文档能支撑老师展示、比赛方案、论文/专利方向说明。

---

## Sources And Research

- `spatial_mapping.py` 已能输出 pixel center、normalized center、bbox、pixel area、clock position、ring id、mileage、local 3D status、source、accuracy level 和 limitations。
- `inspection_report.py` 已能导出 per-frame assessment、evidence、views、multidomain results 和 spatial summary。
- `run_confidence_risk.py` 是现有 selected mask、uncertainty、disagreement、morphology、review priority 的主要来源。
- `docs/plans/2026-06-20-001-feat-spatial-mapping-prototype-plan.md` 和 `docs/competition/spatial-mapping-method.md` 是定位方向的上游基础。
