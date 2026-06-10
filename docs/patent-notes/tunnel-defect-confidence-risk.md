# Tunnel Defect Confidence Risk Patent Notes

## Technical Field

This note describes an image-based tunnel defect segmentation post-processing method. The method is intended for inspection-image analysis, confidence estimation, morphology measurement, and review prioritization.

## Core Method Chain

1. **多姿态一致性预测:** 对同一张隧道图像执行多姿态推理，例如原图、水平翻转等保守变换。
2. **预测反变换与融合:** 将每个姿态下的预测结果反变换回原图坐标，再对类别概率进行融合，得到融合分割候选结果。
3. **不确定性估计:** 基于多姿态预测的概率熵和类别分歧生成不确定性热力图，用于标出需要人工复核的区域。
4. **自适应输出选择:** 根据 single/fused 一致性、前景面积变化、不确定性和分歧图，在 single、fused 和 hybrid 候选中选择 selected mask，避免固定融合压掉小缺陷。
5. **骨架与形态量化:** 从 selected mask 中提取病害区域，计算面积占比、连通域数量、骨架长度、主方向和碎片化程度。
6. **风险分级与复核建议:** 结合病害类别、面积、连通性、骨架长度和不确定性，输出图像级风险等级、类别级解释和复核建议。

## Differentiation

The differentiating point is the complete confidence-risk loop around an existing segmentation model. The method does not only output a mask. It also explains whether the mask is stable under pose changes, quantifies defect morphology, and turns those signals into a review-oriented risk report.

This makes the method easier to demonstrate than a pure network replacement:

- Original single-pass prediction vs. fused prediction vs. selected prediction.
- Selected prediction vs. uncertainty heatmap.
- Selected prediction vs. skeleton/morphology visualization.
- Risk report explaining why manual review is or is not recommended.

SegFormer B1 is treated as a stronger segmentation backbone, while the confidence-risk module is a post-inference enhancement layer. In the current implementation, SegFormer provides same-checkpoint identity/hflip probability maps for fusion, entropy uncertainty, and disagreement. The enhancement claim should remain model-agnostic: a backbone provides candidate masks and probabilities; the post-inference method evaluates consistency, uncertainty, morphology, and review priority.

## Generated Evidence

The implementation can generate the following patent-friendly artifacts:

- `<stem>_single_mask.png`
- `<stem>_fused_mask.png`
- `<stem>_hybrid_mask.png`
- `<stem>_selected_mask.png`
- `<stem>_overlay.png`
- `<stem>_selected_overlay.png`
- `<stem>_uncertainty_heatmap.png`
- `<stem>_disagreement_heatmap.png`
- `<stem>_skeleton.png`
- `<stem>_report.json`
- `summary.json`
- `confidence_risk_eval.json`

For images without ground-truth labels, true mIoU is not available. The implementation may report `Self IoU` between single and fused model outputs as a consistency signal, but this is not a substitute for label-based accuracy.

## Non-Claim Boundary

This module provides image-based defect risk guidance and manual-review suggestions. It does **not** provide structural safety diagnosis, civil-engineering load assessment, or final maintenance decisions. Those remain outside the scope of this implementation and should require expert review.

## Suggested Patent Title

一种基于多姿态预测一致性与不确定性评估的隧道病害分割可信评估及风险分级方法

## Suggested Figure Set

1. System flow: input image, TTA prediction, inverse alignment, fusion, adaptive selection, uncertainty, morphology, risk output.
2. Effect comparison: original image, single-pass prediction, fused prediction, selected prediction.
3. Confidence visualization: selected prediction, uncertainty heatmap, low-confidence regions.
4. Morphology visualization: selected prediction, skeleton view, connected regions.
5. Structured report example: morphology metrics, uncertainty summary, risk level, review suggestion.

## Current Measured Evidence

Using the validation-selected adaptive defaults, the selected output recovers most of the accuracy lost by fixed TTA fusion. On the 150-image test split, `selected_mIoU` is 0.3306 versus 0.3165 for fixed fused output and 0.3305 for the single-pass baseline. On all 1000 labeled samples, `selected_mIoU` is 0.3680 versus 0.3287 for fixed fused output and 0.3725 for single-pass output.

| Scope | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fused | Selected vs single |
|---|---:|---:|---:|---:|---:|---:|
| Test split | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 | +0.0001 |
| All labeled samples | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 | -0.0044 |

The small-defect guard evidence is stronger than a single selected example because it is measured over labeled splits:

| Scope | Protected events | Successful guard events | Fixed fusion harmed | Selected recovered | Protected pixels vs fused |
|---|---:|---:|---:|---:|---:|
| Test split | 99 / 150 | 72 / 150 | 99 / 150 | 81 / 150 | 156,301 |
| All labeled samples | 721 / 1000 | 614 / 1000 | 817 / 1000 | 699 / 1000 | 1,230,616 |

Uncertainty-to-error overlap is reported only when GT masks are available. It supports review prioritization by checking whether high-uncertainty regions coincide with prediction errors:

| Scope | Error coverage | HU error precision | Pixel high-uncertainty threshold | Review fraction threshold |
|---|---:|---:|---:|---:|
| Test split | 45.66% | 80.66% | 0.35 | 0.50 |
| All labeled samples | 44.54% | 78.27% | 0.35 | 0.50 |

This means the patent contribution should be framed as confidence-aware adaptive output selection and explainable risk evidence, not as a newly trained segmentation backbone that universally improves raw single-pass mIoU.

The trained SegFormer B1 checkpoint separately validates backbone segmentation quality. Its final logged evaluation reached `mIoU 84.33`, `mAcc 91.17`, and `aAcc 98.62`, with strong `pipeline`, `vertical`, and `horizontal` classes. This should be presented as backbone evidence, not as proof that the post-inference enhancement itself changes raw segmentation training quality.

For a fuller experiment-ready table, see `docs/experiments/enhancement-evidence-summary.md`.
