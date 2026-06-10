---
title: SegFormer Probability TTA Uncertainty Requirements
date: 2026-06-10
status: active
origin: user selected the next optimization direction after the web demo exposed placeholder uncertainty/disagreement views for SegFormer outputs
---

# SegFormer Probability TTA Uncertainty Requirements

## Summary

Upgrade the SegFormer inference path from "single mask only" to a probability-aware test-time augmentation path. The goal is to make `single mask`, `fused mask`, `selected mask`, `uncertainty heatmap`, and `disagreement map` come from the same SegFormer checkpoint and the same input image, instead of mixing SegFormer masks with placeholder or legacy heatmap artifacts.

This is the highest-leverage next optimization because it directly strengthens the project's main differentiation: confidence-aware adaptive output selection and explainable review evidence.

## Problem

The current SegFormer adapter calls mmseg inference and receives only a final class mask. That makes the Web demo visually strong for segmentation, but weak for the confidence-risk story:

- `single mask` is real SegFormer output.
- `fused mask` currently mirrors the single mask in SegFormer mode.
- `uncertainty heatmap` and `disagreement map` cannot be computed from true SegFormer probabilities yet.
- Demo assets can become inconsistent if masks and heatmaps are generated from different sources.

For a teacher presentation or patent-oriented explanation, every displayed visual should be traceable to the same model source and the same image.

## Desired Outcome

SegFormer should provide enough inference evidence for the existing confidence-risk module to behave as originally intended:

- A single-pass probability tensor and mask.
- A small set of TTA probability tensors aligned back to the original image.
- A fused probability tensor and fused mask.
- A normalized entropy uncertainty heatmap from fused probabilities.
- A disagreement map from aligned TTA masks or probabilities.
- A selected mask chosen by the existing adaptive-selection policy.
- A report that clearly says whether uncertainty/disagreement are computed from real probability evidence.

## Requirements

- R1. The SegFormer inference adapter must expose class probability maps, not only the final argmax mask.
- R2. The adapter must support a conservative TTA set suitable for tunnel defects, starting with identity and horizontal flip.
- R3. Every TTA prediction must be aligned back to the original image coordinate system before fusion or disagreement calculation.
- R4. `fused mask` must be computed from averaged aligned probabilities when probability TTA is enabled.
- R5. `uncertainty heatmap` must be computed from normalized entropy of the fused probability tensor.
- R6. `disagreement map` must be computed from per-pixel differences among aligned TTA predictions.
- R7. The existing `selected mask` logic must consume the real SegFormer `single`, `fused`, `uncertainty`, and `disagreement` inputs without changing its external report shape.
- R8. Web views must only display visuals generated from the same source image and same SegFormer run.
- R9. Upload-only images without GT must still avoid true mIoU, but may show self-consistency, uncertainty, disagreement, morphology, and selected-mask rationale.
- R10. Dataset samples with GT may report label-based metrics separately from uncertainty/disagreement summaries.

## Scope Boundaries

In scope:

- SegFormer inference adapter improvements.
- TTA probability fusion for inference.
- Real uncertainty and disagreement artifacts for Web demo and report JSON.
- A small smoke test or fixture proving the outputs are not placeholder zero maps.
- Updating Web copy only where needed to avoid mixed-source explanations.

Out of scope for this increment:

- Retraining SegFormer.
- Replacing SegFormer B1 with a different backbone.
- Full calibration training or temperature scaling.
- Structural safety diagnosis beyond image-level review guidance.
- Claiming selected output always beats single output in mIoU.

## Acceptance Examples

- AE1. Same-source SegFormer sample
  - **Given:** A dataset sample such as `t1_1` is processed through SegFormer probability TTA.
  - **When:** The Web demo shows single, fused, selected, uncertainty, disagreement, and skeleton views.
  - **Then:** All views correspond to the same defect location and no view uses legacy heatmap assets.

- AE2. Real uncertainty map
  - **Given:** The adapter returns aligned class probabilities for at least identity and horizontal flip.
  - **When:** uncertainty is generated.
  - **Then:** the heatmap has non-trivial value variation and the report marks `uncertainty_available=true`.

- AE3. Real disagreement map
  - **Given:** TTA predictions disagree at boundaries or weak regions.
  - **When:** disagreement is generated.
  - **Then:** the disagreement map highlights those regions and the report marks `disagreement_available=true`.

- AE4. Upload image without GT
  - **Given:** A user drags an unlabeled tunnel image into the Web app.
  - **When:** inference finishes.
  - **Then:** the app shows real SegFormer uncertainty/disagreement and does not show true mIoU.

## Success Criteria

- S1. The SegFormer adapter can produce real probability-backed `uncertainty_heatmap` and `disagreement_heatmap`.
- S2. The Web demo no longer needs placeholder heatmap images for the default SegFormer sample.
- S3. `fused mask` differs from `single mask` when TTA probabilities support a different fused prediction, and matches it when predictions are stable.
- S4. Existing report consumers keep working because artifact names and high-level JSON sections remain stable.
- S5. The project can honestly present the enhancement module as model-aligned with SegFormer, not only as a legacy ResNet-style post-processing path.

## Recommended Next Step

Move this requirements document into a `ce-plan` step. The plan should inspect the mmseg/SegFormer inference API first, because the key implementation decision is how to obtain logits or probability maps from the current Windows-compatible SegFormer stack.

## Sources

- Existing confidence-risk requirements: `docs/brainstorms/2026-06-01-tunnel-defect-confidence-risk-requirements.md`
- Existing SegFormer upgrade requirements: `docs/brainstorms/2026-06-02-segformer-backbone-upgrade-requirements.md`
- Enhancement evidence summary: `docs/experiments/enhancement-evidence-summary.md`
- SegFormer paper: https://arxiv.org/abs/2105.15203
- Test-time augmentation uncertainty paper: https://arxiv.org/abs/1807.07356

