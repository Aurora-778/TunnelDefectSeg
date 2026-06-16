# 隧道病害分割结果可信复核方法交底书草稿

## 1. 技术领域

本草稿涉及隧道巡检图像处理、语义分割结果可信评估和人工复核排序。更具体地说，本文描述一种围绕已有分割模型输出的 post-inference confidence review 方法：输入隧道图像和模型候选结果，输出 `selected mask`、uncertainty、disagreement、morphology_delta、review priority 和可复核证据。

本方法不限定具体分割 backbone。当前实施例使用 SegFormer B1 作为 `mask source`，但方法也可以接入其他能够输出类别概率或候选 mask 的语义分割模型。

## 2. 背景技术问题

隧道病害分割系统通常输出一张最终 `mask`。这个输出能告诉使用者模型画出了哪里，但不能充分回答以下问题：

- 该 `mask` 在轻量扰动或候选融合后是否稳定。
- fixed fusion 是否把小病害或细长病害压小、压断或误删。
- 病害区域边界、形态和骨架是否发生明显变化。
- 哪些图像或区域应该优先交给人工复核。
- 无 GT 上传图片无法计算 true mIoU 时，系统还能给出哪些可信信号。

因此，本方法的技术目标不是重新发明 SegFormer 或单纯追求 raw mIoU，而是在分割结果之后增加一条可解释、可复现的可信复核链条。

## 3. 方法步骤

### S1. 图像输入与候选预测生成

获取隧道巡检图像，调用语义分割模型生成至少一个候选预测结果。候选结果可以包括原图单次预测 `single mask`、基于增强视角的概率图、以及由候选概率得到的类别 mask。

当前实施例中，SegFormer B1 生成 identity 和 horizontal flip 两个同源 probability maps。该实施例仅用于说明候选结果来源，不构成对 backbone 的限定。

### S2. 多姿态概率对齐

对同一图像的不同推理姿态进行反变换，使各姿态下的 probability maps 回到原图坐标系。对齐后的概率图用于后续 fixed fusion、uncertainty 和 disagreement 计算。

### S3. 候选 mask 融合

根据对齐后的 probability maps 计算 `fused mask`，并可生成 `hybrid mask` 作为候选结果。系统保留 `single mask`、`fused mask` 和其他候选 mask，而不是只保留一个最终结果。

### S4. 融合损伤识别

比较 `single mask` 与 `fused mask` 的前景面积、前景一致性、protected pixels、候选 self-consistency 和 selected-vs-fused 变化。若 fixed fusion 导致前景明显收缩、细小病害消失或候选一致性下降，则标记可能存在融合损伤。

### S5. 自适应 selected mask 选择

根据融合损伤、uncertainty、disagreement、候选 self-consistency 和候选形态变化，从候选 mask 中选择 `selected mask`。当 fixed fusion 稳定且未压缩病害时，可以选择 `fused mask`；当 fixed fusion 可能压掉小病害时，可以回退到 `single mask` 或选择更保守的候选结果。

### S6. 不确定性与分歧度分析

基于候选 probability maps 或多姿态预测结果计算 entropy uncertainty 和 TTA disagreement。输出 uncertainty heatmap、disagreement heatmap 和结构化统计字段，用于定位不稳定区域和支撑人工复核。

有 GT 的数据集样本可进一步评估 uncertainty 与真实错误区域的重合关系，例如 `Error coverage`、`HU error precision` 和 calibration gap。无 GT 上传图片不生成这些真实误差指标。

### S7. 形态变化分析

对 `single mask`、`fused mask` 和 `selected mask` 计算 `morphology_delta`。形态特征包括但不限于：

- 前景面积变化。
- 连通域数量变化。
- 骨架长度变化。
- 主方向变化。
- fragment 或断裂趋势。

该步骤用于解释候选 mask 是否出现缩小、断裂、方向漂移或 selected 恢复。

### S8. 复核优先级生成与证据输出

综合 risk、uncertainty、disagreement、self-consistency、融合损伤、形态变化和 selected-mask reason，生成 `review priority` 和 review queue。系统输出多视图 artifact、结构化 report、evidence summary 和 patent evidence pack。

`review priority` 表示建议人工优先复核的排序信号，不表示结构安全诊断，也不替代人工验收或养护决策。

当前实施例中，`review priority` 使用可解释加和规则：

```text
review_priority_score = sum(active score components)
```

其中 active score components 包括：

- `image_risk_low / image_risk_medium / image_risk_high`：图像级 risk 对复核排序的贡献。
- `risk_review_required`：risk 模块已明确建议人工复核。
- `defect_uncertainty`：缺陷区域平均 uncertainty 超过阈值。
- `defect_high_uncertainty_fraction`：缺陷区域中高 uncertainty 像素比例超过阈值。
- `defect_disagreement`：多候选预测的 disagreement 超过阈值。
- `low_self_consistency / severe_self_consistency_drop`：single/fused 前景一致性较低。
- `fused_foreground_shrinkage`：fixed fused 相比 single 出现前景收缩。
- `adaptive_selection_non_fused`：adaptive selection 放弃 fixed fused，选择 single 或 hybrid。

结构化报告中的 `review_priority.formula.components` 记录实际触发的分数组件，`review_priority.formula.thresholds` 记录阈值，`review_priority.formula.priority_bins` 记录分桶规则。这样可以从最终分数追溯到具体算法因素。

## 4. 系统模块

| 模块 | 输入 | 输出 | 对应步骤 |
|---|---|---|---|
| 候选预测生成模块 | 隧道图像、分割模型 | `single mask`、probability maps | S1 |
| 概率对齐与融合模块 | 多姿态 probability maps | `fused mask`、`hybrid mask` | S2-S3 |
| 融合损伤识别模块 | 候选 masks、前景统计 | shrink、protected pixels、self-consistency | S4 |
| 自适应选择模块 | 候选 masks、损伤信号、不确定性 | `selected mask`、selection reason | S5 |
| 不确定性与分歧分析模块 | 对齐 probability maps | uncertainty、disagreement | S6 |
| 形态变化分析模块 | `single/fused/selected mask` | `morphology_delta` | S7 |
| 复核排序模块 | risk、uncertainty、disagreement、morphology | `review_priority`、review queue | S8 |
| 证据导出模块 | report、evaluation JSON | evidence summary、patent pack、case pack | S8 |

## 5. 技术效果

本方法的有益效果应围绕可信复核表达：

- 保留 single/fused/selected 候选关系，避免 fixed fusion 损伤被最终 mask 掩盖。
- 在 fixed fusion 压缩小病害时，通过 adaptive selection 恢复或保护前景证据。
- 在无 GT 上传场景下仍能输出 self-consistency、uncertainty、disagreement、morphology_delta 和 review priority。
- 在有 GT 的数据集上，可验证 high-uncertainty 区域与真实错误之间的关系。
- 通过 review queue 将不稳定样本前置，降低人工复核时遗漏高风险预测的概率。
- 通过 morphology_delta 解释候选结果的面积、连通域、骨架和方向变化，使复核理由更可读。

## 6. 当前实施例证据

当前仓库中可复用的证据来源包括：

- `docs/experiments/enhancement-evidence-summary.md`
- `experiments/enhancement_evidence_test_summary.json`
- `experiments/enhancement_evidence_all_summary.json`
- `experiments/patent_evidence_test_pack.json`
- `docs/solutions/best-practices/enhancement-evidence-from-evaluation-json.md`
- `docs/solutions/documentation-gaps/patent-evidence-artifact-contracts.md`

已记录的代表性结论包括：

- SegFormer B1 作为 backbone，在 160000 iter 时达到 `mIoU 84.33`、`mAcc 91.17`、`aAcc 98.62`。该数字只作为 backbone evidence。
- 在 test split 上，`selected mIoU` 为 `0.3306`，`fixed fused mIoU` 为 `0.3165`，selected 相对 fixed fused 增加 `+0.0141`。
- 在 all labeled samples 上，`selected mIoU` 为 `0.3680`，`fixed fused mIoU` 为 `0.3287`，selected 相对 fixed fused 增加 `+0.0393`。
- 在 all labeled samples 上，protected pixels vs fused 为 `1,230,616`，说明 fixed fusion shrink 是可量化现象。

这些证据支持“恢复 fixed fused 损伤、保护小病害和生成复核证据”，不应表述为 selected 永远超过 single。

## 7. 无 GT 场景边界

用户拖拽自选图片时通常没有 `GT mask`。此时系统可以输出：

- `Self IoU`
- uncertainty
- disagreement
- morphology and morphology_delta
- selected-mask reason
- review priority

但系统不能输出或暗示：

- true mIoU
- error overlap
- uncertainty calibration against real errors
- label-based accuracy

## 8. 非主张边界

本方法不主张以下内容：

- 不主张发明 SegFormer、TTA、entropy uncertainty、skeletonization 或 mIoU 指标。
- 不提供结构安全诊断。
- 不进行土木工程承载力评估。
- 不自动作出养护决策。
- 不替代人工验收或专家复核。
- 不保证 selected mask 在所有场景下均优于 single-pass mask。

## 9. 建议附图

1. 方法流程图：输入图像、候选预测、概率对齐、融合、融合损伤识别、自适应选择、不确定性/分歧分析、形态变化、复核排序。
2. 系统模块图：候选预测生成模块、融合模块、自适应选择模块、形态模块、复核排序模块、证据导出模块。
3. 效果对比图：原图、single mask、fused mask、selected mask、selected overlay。
4. 不确定性和分歧图：uncertainty heatmap、disagreement heatmap、review reason。
5. 形态变化图：mask、skeleton、area/components/skeleton length delta。
6. 复核队列示意图：top-k review queue、trigger reasons、GT availability、artifact availability。

## 10. 交底书使用说明

本文是研发侧草稿，用于向老师、专利代理人或论文写作材料说明技术路线。正式提交前应进一步完成查新、权利要求法律化表达和数字来源复核。
