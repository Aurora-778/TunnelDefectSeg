---
title: SegFormer Backbone Upgrade Requirements
date: 2026-06-02
status: active
origin: user request to replace the weak ResNet50/FCN predictor after blocky masks and low mIoU
---

# SegFormer Backbone Upgrade Requirements

## Problem

The current ResNet50/FCN segmentation baseline localizes the `t1_1` defect roughly correctly, but predicts the annotated bent shape as a block-like region. This weak boundary and thin-structure behavior also explains the low multiclass mIoU, especially for vertical and horizontal disease classes.

The adaptive confidence-risk module should remain valuable, but it should sit on top of a stronger segmentation backbone rather than trying to fix missing shape detail through post-processing alone.

## Desired Outcome

Replace the practical segmentation backbone direction with SegFormer B1 while preserving the existing confidence-risk story:

- SegFormer produces the main single-pass mask.
- TTA, adaptive selection, uncertainty, disagreement, morphology, skeleton, and risk scoring remain the differentiating post-inference module.
- Evaluation reports compare ResNet50, fixed fused, adaptive selected, and SegFormer-based outputs when labels are available.
- The web demo can show GT, ResNet/adaptive output, and SegFormer output side by side once a SegFormer checkpoint exists.

## Requirements

- R1. Export the existing 6-class dataset into an mmseg-compatible layout using the same deterministic `700 / 150 / 150` split.
- R2. Preserve the six class ids: `background`, `simple`, `blocky`, `pipeline`, `vertical`, `horizontal`.
- R3. Generate a SegFormer B1 config with `num_classes=6`; do not accidentally use the 2-class config from the other desktop project.
- R4. Do not require `mmcv`, `mmseg`, or `timm` just to prepare data or run unit tests in the current repository.
- R5. Provide Windows-friendly launch scripts for training and test evaluation in a SegFormer environment.
- R6. Keep the confidence-risk module model-agnostic enough that a trained SegFormer checkpoint can become the source of masks later.

## Scope Boundaries

In scope for the first step:

- Dataset export.
- SegFormer B1 config generation.
- Training and test launcher generation.
- Documentation of the intended workflow.

Out of scope for the first step:

- Installing the SegFormer runtime environment.
- Running a full 160k-iteration training job inside this turn.
- Claiming better mIoU before a trained checkpoint is evaluated.

## Success Criteria

- `segformer_tools.py` can prepare a complete mmseg dataset and config without external SegFormer dependencies.
- The generated split counts are `700 / 150 / 150` on the full dataset.
- The generated config is explicitly 6-class, not binary.
- The next planning step can focus on training/evaluation and plugging the checkpoint into `run_confidence_risk.py`.

## Recommendation

Use SegFormer B1 as the next backbone baseline, then connect the trained checkpoint to the existing adaptive confidence-risk module. This gives the project a stronger visual and metric foundation while keeping the patentable contribution centered on confidence-aware adaptive decision and risk reporting.
