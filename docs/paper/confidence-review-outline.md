# Confidence Review Paper Outline

## Working Title

面向隧道病害分割结果的可信复核方法研究

Alternative English title:

Confidence-Aware Review Prioritization for Tunnel Defect Segmentation Outputs

## One-Sentence Summary

本项目研究的不是重新发明一个 segmentation backbone，而是在已有隧道病害分割模型之后，利用 candidate masks、uncertainty、disagreement、morphology_delta 和 review priority 判断哪些结果更可信、哪些需要人工优先复核。

## Research Question

RQ1. 固定 TTA fusion 是否会压缩或丢失小病害前景？

RQ2. adaptive selected mask 是否能恢复 fixed fused 带来的损失？

RQ3. uncertainty、disagreement、self-consistency 和 morphology_delta 能否形成可解释的人工复核排序信号？

RQ4. 在无 GT 上传图片中，系统能否输出可信复核信号，同时避免伪造 true mIoU？

## Contribution Claims

1. 提出一条 model-agnostic post-inference confidence review chain：candidate mask generation -> fusion harm detection -> adaptive selected mask -> uncertainty/disagreement -> morphology_delta -> review priority。
2. 证明 fixed fusion 会在部分样本上压缩病害前景，而 adaptive selection 能恢复 fixed fused 的一部分损失。
3. 将 morphology_delta 从可视化解释提升为结构化算法证据，输出 area shrinkage、component fragmentation、skeleton change 和 direction shift 等 flags。
4. 提供 review queue，用模型内部证据进行排序，再用 GT-derived flags 评估高优先级 bucket 是否覆盖 fixed fusion harm 或真实错误。
5. 明确 GT/no-GT 边界：无 GT 上传图片只输出 self-consistency、uncertainty、disagreement、morphology_delta 和 review priority，不输出 true mIoU。

## Paper Structure

### 1. Introduction

- 隧道病害图像分割可以帮助巡检人员定位病害区域。
- 仅输出 mask 不足以说明结果是否稳定、是否需要人工复核。
- TTA fusion 虽然常用于提高稳定性，但在小病害或细长病害上可能产生前景收缩。
- 本文关注 post-inference confidence review，而不是重新设计分割主干。

### 2. Related Work

需要后续补充正式引用，当前只列主题方向：

- semantic segmentation for tunnel defect or crack inspection
- SegFormer and transformer-based segmentation
- test-time augmentation for segmentation
- uncertainty estimation from entropy or prediction disagreement
- morphology/skeleton-based crack or defect measurement
- human-in-the-loop review and triage

Integrity note: this section should not cite papers until the exact paper has been checked. Do not leave fabricated citations.

### 3. Method

#### 3.1 Candidate Mask Generation

Use a segmentation model as `mask source`. The current implementation uses SegFormer B1, but the method is not limited to that backbone.

Outputs:

- `single mask`
- aligned probability maps
- `fused mask`
- optional `hybrid mask`

#### 3.2 Fusion Harm Detection

Compare single/fused outputs using:

- foreground shrink pixels
- fused-to-single area ratio
- self foreground IoU
- protected pixels vs fused

#### 3.3 Adaptive Selected Mask

Select `single`, `fused`, or `hybrid` according to fusion stability and small-defect protection rules.

Key principle: selected is not claimed to always beat single. The main target is avoiding fixed fusion harm.

#### 3.4 Uncertainty and Disagreement

Compute:

- entropy uncertainty from probability maps
- TTA disagreement from candidate prediction differences
- GT-based error overlap only on labeled data

#### 3.5 Morphology Delta

Compare `single -> fused`, `fused -> selected`, and `single -> selected`.

Structured evidence:

- `area_shrinkage`
- `area_expansion`
- `component_fragmentation`
- `component_simplification`
- `skeleton_shortening`
- `skeleton_lengthening`
- `direction_shift`

#### 3.6 Review Priority

`review_priority.score` is the sum of active explainable components:

- image risk
- risk review required
- defect uncertainty
- high-uncertainty fraction
- disagreement
- low or severe self-consistency drop
- fused foreground shrinkage
- adaptive selection chose non-fused output

The output is a review-ordering signal, not structural safety diagnosis.

### 4. Experiments

#### 4.1 Dataset and Backbone

- 6-class tunnel defect dataset.
- `train / val / test = 700 / 150 / 150`.
- SegFormer B1 final logged result: `mIoU 84.33`, `mAcc 91.17`, `aAcc 98.62`.
- This is backbone evidence, not enhancement gain.

#### 4.2 Enhancement Evaluation

Measured variants:

- `single`
- `fixed_fused`
- `selected`
- `full_review_priority`
- review-priority baselines: `uncertainty_only`, `shrinkage_only`, `self_iou_instability_only`

Pending variants:

- `selected_without_morphology`
- `selected_without_uncertainty_disagreement`
- `review_priority_without_morphology_delta`

#### 4.3 Metrics

GT-required:

- true mIoU
- class IoU
- error overlap
- uncertainty calibration

No-GT signals:

- self-consistency
- uncertainty
- disagreement
- morphology_delta
- review priority

### 5. Results

Current measured results:

- Test split: selected vs fixed fused mIoU `+0.0141`.
- All labeled samples: selected vs fixed fused mIoU `+0.0393`.
- Test split protected pixels vs fused: `156,301`.
- All labeled samples protected pixels vs fused: `1,230,616`.
- Test split HU error precision: `80.66%`.
- All labeled samples HU error precision: `78.27%`.
- Review-priority baseline comparison is available in `review_queue_summary.baseline_comparison`.

Interpretation:

- adaptive selection recovers fixed fused loss.
- high uncertainty is a useful review signal on labeled data.
- full review priority is a multi-objective queue; it should be compared against single-signal queues rather than claimed as universally dominant.
- selected does not universally beat single; this is a limitation and should be stated.

### 6. Case Study

Use `docs/experiments/patent-case-pack.md`:

- C1 fixed fusion harmed / selected recovered.
- C2 model-internal stable fused / GT caution.
- C3 high uncertainty / error overlap.
- C4 top review queue sample.
- C5 limitation case.
- C6 and C7 pending until visual artifacts are generated.

### 7. Limitations

- Current ablations without morphology or without uncertainty/disagreement are pending.
- Current compact representative cases have path templates; visual artifacts still need generation.
- The method is image-based review guidance, not structural safety diagnosis.
- GT-derived metrics are unavailable for drag-and-drop upload images.
- `blocky` remains the bottleneck class in the SegFormer backbone.

### 8. Conclusion

The project should conclude that a trained segmentation backbone can be wrapped by an explainable confidence-review layer. The strongest defensible claim is not raw mIoU dominance, but fixed-fusion harm detection, selected-mask recovery, structured morphology evidence, and review prioritization.

## Figure Plan

| Figure | Content | Source |
|---|---|---|
| Fig. 1 | method flow | `docs/patent-notes/confidence-review-disclosure.md` |
| Fig. 2 | system modules | `docs/patent-notes/confidence-review-disclosure.md` |
| Fig. 3 | single/fused/selected comparison | `docs/experiments/patent-case-pack.md` C1 |
| Fig. 4 | uncertainty and review case | `docs/experiments/patent-case-pack.md` C3 |
| Fig. 5 | review queue | `experiments/patent_evidence_test_pack.json` |
| Fig. 6 | limitation case | `docs/experiments/patent-case-pack.md` C5 |

## Writing Boundary

Do not write fabricated citations. Do not claim:

- SegFormer is invented here.
- selected always beats single.
- review priority is structural safety diagnosis.
- no-GT upload results have true mIoU.
