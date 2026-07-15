---
date: 2026-07-15
topic: real-inspection-association-pilot
---

# TunnelDefect Real Inspection Association Pilot Requirements

## Summary

下一阶段建设一个最小真实双轮巡检 Pilot：用薄数据 Adapter 接入带 mask 和工程元数据的两轮巡检记录，并用独立 Ground Truth 评估 history-only no-id Association。重点是获得第一组可复核的真实跨巡检证据，而不是继续增加 Web、Agent 或仿真展示。

---

## Problem Frame

当前系统已经具备分割、工程化描述、history-only Association、Memory、可信度门控、复检报告和 Web 展示，但核心跨巡检证据仍来自 KICT 静态 mask 与仿真元数据。困难 benchmark 只有 18 个合成案例，`weighted_no_id` 的 top-1 accuracy 为 0.9333，未超过 `spatial_only` 的 1.0000；继续在合成 fixture 上调规则，容易得到只适配当前案例的结果。

系统当前最大的短板不是页面数量，也不是编排能力，而是缺少真实双轮观测、独立跨巡检标签和可重复评估入口。若没有这三项，视觉特征、误差模型或新匹配策略都无法形成可信实验结论。

---

## Candidate Directions

| Direction | Value | Main risk | Decision |
|---|---|---|---|
| 手工映射一组双轮小样 + GT 质量审计 | 最快验证字段、标签和评估是否成立 | 自动化程度低，但失败成本最低 | **推荐的第一道门** |
| 薄 Adapter + Association GT evaluator | 把已验证的小样流程自动化并形成可重复评估 | 依赖真实样本和标注质量 | 小样审计通过后实施 |
| 轻量视觉特征匹配 | 可能降低相邻病害误匹配 | 没有真实 GT 时只能继续拟合合成案例 | Pilot 后做消融 |
| 可比性校准与误差建模 | 能把 `verified_comparable` 从标签变成测量结论 | 需要重复拍摄、配准和尺度信息 | 积累重复观测后开展 |
| Web 上传与异步任务 | 提升演示和部署体验 | 不增加算法证据，扩大维护面 | 暂缓 |

---

## Key Decisions

- **先建立评测证据，再改算法。** 第一阶段不增加视觉分数或新阈值，只验证现有 `weighted_no_id`、`spatial_only` 等策略在同一真实 Pilot 上的表现。
- **先手工跑通一组小样，再自动化。** 若一组真实双轮观测无法稳定映射到现有字段或 GT 无法一致标注，停止建设 Adapter，先修数据合同。
- **本地观测标识与跨轮 GT 分离。** 算法输入只包含每轮局部观测标识；全局病害对应关系保存在独立 GT 表中，只在 Association 完成后用于计分。
- **只做双轮评估 Pilot，不伪装成生产接入。** Adapter 面向已标注的离线研究数据，不声称支持无标注在线巡检或自动形成永久病害 ID。
- **真实 mask 不自动获得纵向可比资格。** 没有配准残差、尺度校准和重复测量证据时，`comparability_status` 仍不得设为 `verified_comparable`，本阶段不输出方向性 Growth 结论。
- **复用现有 history-only Association。** 不建设第二套 Agent、DAG、registry 或匹配框架，也不改变生产 no-id 评分公式。

---

## Key Flows

- F1. **Pilot validation**
  - **Trigger:** 提供至少两轮巡检的图像、mask 和 metadata；GT 可后补。
  - **Steps:** 先独立校验目录、键唯一性、路径、时间顺序和数值范围；若提供 GT，再单独校验审计状态与引用完整性。
  - **Outcome:** 合法观测可在无 GT 时进入 Adapter；没有已审计 GT 时只能推理，不能评分或形成 headline 结论。
- F2. **Observation adaptation**
  - **Trigger:** 数据合同校验通过。
  - **Steps:** 从 mask 提取面积和 bbox；生成中性 observation records；再生成现有 Association 可消费的兼容 frame records。
  - **Outcome:** 算法输入不包含全局 GT，且来源和可比性边界可追踪。
- F3. **History-only evaluation**
  - **Trigger:** 兼容 frame records 和带显式 `review_status` 的 sequence manifest 已生成；独立 GT 仅在评分时需要。
  - **Steps:** 首轮只建 baseline；后续轮次只使用历史候选。无 GT 时结束于 inference artifacts；有已审计 GT 时才在匹配后连接标签计算指标和错误案例。
  - **Outcome:** 推理与 GT 解耦；满足 headline 准入条件时生成 no-id 与 baseline 对照指标，不形成真实 Growth claim。

---

## Requirements

**Pilot data contract**

- R1. Pilot 必须至少包含一个 `sequence_id` 下的两个按时间排序巡检，每条病害观测具有图像、mask、时间、里程、环号、方位和局部观测标识。
- R2. 复合键和局部观测标识必须唯一；缺文件、重复键、非法时间、非法数值和跨轮顺序错误必须在运行前明确报错。同一 sequence 的巡检时间区间必须严格不重叠，无法确定先后顺序时拒绝运行。
- R3. mask 非零像素用于提取面积、bbox、中心点和尺寸；空 mask 保留在 observation audit 中并显式标记，但不得进入 Association frame records、候选池或任何指标分母。任一巡检过滤后没有有效观测时，该 sequence 不得运行 Association。
- R4. 所有派生行必须保留 `observation_source`、源 inspection、局部观测键和固定 `data_contract_version=real_inspection_pilot_v1`。

**Label quarantine**

- R5. Association 输入中的 `disease_id` 必须由 `sequence_id + association_inspection_id + local_observation_id` 生成，不得直接使用跨轮全局病害标签。
- R6. 跨轮 GT 必须存放在独立表中，并以局部观测键映射到 `global_disease_id`；development/evaluation 分组存放在独立 sequence manifest，不由 GT 表决定推理分组。正式 GT 必须与 `gt_audit.csv` 的最终裁决逐行一致，manifest 记录审计版本与哈希。
- R7. `global_disease_id` 只能在 Association 结果生成后用于评估，不得进入 no-id score、ranking、match type、confidence、conflict 或候选过滤。

**Evaluation**

- R8. Adapter 必须在每个 `sequence_id` 内按最早时间归一化生成 `I0001`、`I0002` 等 Association inspection ID，并保留原始巡检 ID；每个 sequence 单独调用现有 history-only coordinator，禁止跨 sequence 候选。
- R9. 同一 Pilot 只比较 `spatial_only` 与生产 `weighted_no_id`；本阶段不生成 with-id upper-bound，避免把独立 GT 送入算法输入。
- R10. 报告至少包含 top-1 accuracy、accepted accuracy、rejection rate、manual review rate、false match、false reject、缺标签数量和稳定排序的错误案例，并遵守本文的指标分母与 GT 完整性定义。
- R11. 任意 inference/evaluation runner 都必须读取带显式 `review_status` 的独立 sequence manifest，缺失状态不得默认成 pending。默认使用冻结公式进行锁定评估，不在同一 Pilot 上调参；若存在多个独立 `sequence_id`，split 必须按 sequence 分组。label-dependent headline 只来自同时满足 `split=evaluation`、`review_status=approved`、`audit_complete=true`、`gt_complete=true` 的 sequences。

**Claim boundaries and isolation**

- R12. 本阶段默认 `comparability_status=not_longitudinally_comparable`；只有后续独立测量校准通过才允许 `verified_comparable`。
- R13. Pilot 不生成增长、减小、稳定或长期演化结论；面积只作为 Association 特征和静态审计证据。
- R14. 所有真实 Pilot 派生数据和评估产物写入独立目录，不覆盖 `data/simulated/`、正式 benchmark 或主 pipeline 输出。
- R15. 本阶段不得修改 Association 评分公式、Memory/Growth 核心规则、`config/dag.yaml`、Web、模型训练或视频链路。

---

## Acceptance Examples

- AE1. **Covers R2, R5-R7.** GT 中同一真实裂缝在 sequence S01 的 I001/I002 局部标识分别为 `S01::I001::C03` 和 `S01::I002::C07`；Association 只看到局部标识，计分完成后 evaluator 才把两者映射到同一个 `global_disease_id`。
- AE2. **Covers R8.** I002 的 query 只能看到 I001 baseline；即使 I003 文件已经存在，也不能出现在 I002 候选或 history provenance 中。
- AE3. **Covers R3, R12-R13.** 两轮 mask 面积不同但没有尺度校准时，派生记录仍为不可纵向比较，报告只保留面积审计，不生成增长结论。
- AE4. **Covers R9-R10.** 若 `weighted_no_id` 未超过 `spatial_only`，报告必须如实呈现，并保留导致 false match、false reject 或人工复核的案例。
- AE5. **Covers R14.** 测试和 Pilot 运行前后，`data/simulated/`、`outputs/association_benchmark/` 和现有正式报告的内容哈希保持不变。

---

## Metric Semantics

- **Headline eligibility:** `split=evaluation`、`review_status=approved`、`audit_complete=true`、`gt_complete=true` 四项必须同时成立；未裁决、单人复核、审计缺行或 GT 与裁决不一致均不得进入 headline。
- **GT-complete sequence:** sequence 中每个 query 和所有可进入历史候选池的观测都有唯一 `global_disease_id`。
- **Match-available query:** 在 headline-eligible sequence 中，query 的同一 `global_disease_id` 至少已在历史候选池出现一次。
- **Set-valued top-1:** 使用阈值决策前的 rank-1 候选；多个历史局部观测可属于同一 `global_disease_id`，命中其中任意一个均为正确。match-available query 没有候选时计错误。`top1_accuracy = top1_correct / match_available_count`。
- **Accepted accuracy:** 只统计 headline-eligible sequences 中自动接受的记录；分母为这些自动接受记录数。
- **False match:** 在 headline-eligible sequence 中，自动接受错误全局标签，或自动接受历史中确认没有同标签候选的新病害。人工复核不计 false match；`false_match_rate` 分母为自动接受记录数。
- **False reject:** match-available query 输出 reject；确认的新病害被 reject 是正确拒识。`false_reject_rate` 分母为 `match_available_count`。
- **Missing label:** 任一 query 或历史候选缺 GT 时计入 `missing_label_count`，该 sequence 标记 `gt_complete=false`，不得进入 label-dependent headline；可单独列出探索性案例，但不能与 evaluation headline 混合。
- **Operational metrics:** `rejection_rate` 和 `manual_review_rate` 不依赖 GT，分母为全部 Association-eligible query records（不含空 mask 和被排除观测），并按 split 单独报告；该区块不得标成 accuracy headline，也不得与 label-dependent headline 使用同一个样本范围标签。
- **Zero denominator:** 任一分母为 0 时，对应比率写为 `null` 并保留 denominator=0，不伪装成 0% 性能。

---

## Success Criteria

- 一个至少双轮的 Pilot 能从原始 image/mask/metadata 生成隔离的 observation records、兼容 frame records 和 GT manifest。
- 自动测试证明 `global_disease_id` 未进入 Association 输入或 no-id 打分路径。
- 真实 Pilot 评估报告能在同一数据上比较 `weighted_no_id` 与 `spatial_only`，并公开全部拒识、复核和错误案例。
- 没有真实 Pilot 数据时，代码仍可用 `tmp_path` 构造的小型 fixture 完成合同和泄漏测试，但文档不得把该测试称为真实验证。
- 主 pipeline、现有 benchmark、Web 和正式 artifacts 不受影响，全量测试继续通过。

---

## Scope Boundaries

本阶段不做：

- Web 上传、异步任务、前后端拆分或蓝鲸部署；
- 新模型、训练、视觉 embedding、DINOv2 或模型权重下载；
- Hungarian、全局 one-to-one assignment、tracker 或 SLAM；
- Growth 阈值调整、配准误差模型或最小可检测变化；
- 通用 Adapter registry、插件系统、数据库、LLM 或多 Agent；
- 无标注生产巡检和永久 ID 自动分配。

---

## Dependencies / Assumptions

- 至少需要两轮同一路段数据；只有随机静态图片时只能验证 Adapter，不能完成 Association 真实评估。
- 每轮观测需要人工局部标注，跨轮 GT 需要人工确认；GT 质量优先于样本数量。
- 第一组真实数据必须先完成手工字段映射和双人/复核式 GT 审计；审计记录固定保存为 Pilot 隔离目录内的 `audit/gt_audit.csv`，至少记录两位复核标识、分歧与最终裁决。`review_status` 统一使用 `audit_pending | approved | audit_failed`：pending 允许 Adapter 和 inference-only 运行但不得评分，failed 只允许输出校验/审计错误且禁止生成推理与评分产物，只有 approved 才可能形成 headline。
- mask 面积是像素级描述量，没有相机标定和配准时不代表真实物理尺寸变化。

---

## Sources / Research

- `README.md`
- `docs/brainstorms/2026-07-15-credibility-projection-closure-requirements.md`
- `outputs/association_benchmark/association_benchmark_report.md`
- `outputs/association_evaluation_report.md`
- `orchestrator/history_only_association.py`
- `orchestrator/agents/association_agent.py`
- `scripts/analyze_disease_growth.py`
- `tests/test_history_only_association.py`
- `tests/test_association_benchmark.py`
