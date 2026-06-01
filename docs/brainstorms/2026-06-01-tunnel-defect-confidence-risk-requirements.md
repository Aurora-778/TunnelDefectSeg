---
date: 2026-06-01
topic: tunnel-defect-confidence-risk
---

# Tunnel Defect Confidence Risk Requirements

## Summary

Add a post-inference module for the existing tunnel defect segmentation baseline that turns a single model prediction into a confidence-aware risk assessment. The module should produce a fused segmentation mask, uncertainty heatmap, defect morphology measurements, risk level, and review suggestion without requiring model retraining in v1.

---

## Problem Frame

The current project has a working six-class tunnel defect segmentation baseline with training, evaluation, and image-level inference outputs. The baseline already reports useful metrics, but a single segmentation mask does not tell an operator which regions are reliable, which regions need manual review, or how severe a detected defect appears in engineering terms.

For patent-oriented differentiation, the valuable move is not simply replacing the backbone or adding another segmentation network. A stronger v1 is an interpretable module that combines multi-pose prediction consistency, uncertainty estimation, skeleton-based morphology, and risk grading. That shape is easier to run on Windows, easier to demonstrate with before/after artifacts, and easier to describe as a complete technical method.

---

## Key Decisions

- **Post-inference first.** v1 extends the existing trained model output instead of changing the training pipeline. This keeps the work easy to implement and makes the comparison against the current baseline clean.
- **Confidence plus risk, not accuracy alone.** The patent framing should emphasize confidence-aware defect assessment and review guidance, with accuracy improvements as supporting evidence.
- **Interpretable scoring.** Risk grading should be driven by visible quantities such as area, skeleton length, connected components, direction, and prediction uncertainty rather than an opaque learned score.
- **Windows-stable dependencies.** The module should avoid custom CUDA kernels, C++ extensions, and topology packages that are likely to fail on Windows. Preference goes to ordinary Python image-processing operations already compatible with the project style.

---

## Requirements

**Multi-pose prediction and fusion**

- R1. The module must accept one or more tunnel inspection images and run the existing trained segmentation model over each input.
- R2. The module must support a small fixed set of inference-time image poses or perturbations, such as flips, small rotations, scale changes, or brightness changes, then align every prediction back to the original image coordinate space.
- R3. The module must fuse aligned predictions into a final class mask that can be compared against the original single-pass prediction.
- R4. The module must compute a pixel-level uncertainty map from disagreement across the aligned predictions.

**Morphology and topology measurement**

- R5. The module must extract defect regions from the fused mask and compute per-class measurements including area ratio, connected-component count, approximate skeleton length, dominant direction, and fragmentation indicators where meaningful.
- R6. The module must separately highlight low-confidence defect regions so a reviewer can distinguish uncertain segmentation from stable defect evidence.
- R7. The module must treat background and defect classes differently: background uncertainty is useful for visualization, but risk scoring should be driven by detected defect regions.

**Risk grading and review output**

- R8. The module must assign each processed image an overall risk level using interpretable rules over defect extent, morphology, class type, and uncertainty.
- R9. The module must emit per-class or per-region review suggestions, especially when a defect class has high uncertainty, fragmented skeletons, or large connected regions.
- R10. The module must produce human-inspectable artifacts: fused mask, overlay image, uncertainty heatmap, and a structured report containing measurements and risk results.

**Evaluation and patent support**

- R11. The module must support side-by-side comparison between single-pass prediction and multi-pose fused prediction.
- R12. The module must expose enough metrics to support a small experimental section: fused mIoU or per-class IoU where labels are available, uncertainty summary statistics, region count changes, and qualitative heatmap examples.
- R13. The v1 output should be suitable for patent figures: input image, original prediction, fused prediction, uncertainty heatmap, skeleton/morphology view, and risk report.

---

## Key Flows

- F1. Confidence-aware inference
  - **Trigger:** A user provides one tunnel inspection image or a folder of images.
  - **Steps:** The existing model predicts multiple posed variants; predictions are aligned; class probabilities or masks are fused; disagreement becomes uncertainty.
  - **Outcome:** The user receives a fused defect mask and uncertainty heatmap.
  - **Covers:** R1, R2, R3, R4, R10

- F2. Morphology measurement
  - **Trigger:** A fused mask is available for an image.
  - **Steps:** Defect regions are separated by class; each region is measured for area, connectivity, skeleton length, dominant direction, and fragmentation.
  - **Outcome:** The user receives interpretable quantities that describe the defect shape and severity.
  - **Covers:** R5, R6, R7

- F3. Risk grading and review suggestion
  - **Trigger:** Morphology and uncertainty measurements are available.
  - **Steps:** The module combines defect class, extent, topology, and uncertainty into a risk level and review suggestion.
  - **Outcome:** The user receives an image-level risk result and a concise explanation of why it was assigned.
  - **Covers:** R8, R9, R10, R13

---

## Acceptance Examples

- AE1. Single image with stable defect
  - **Covers:** R3, R4, R8, R10
  - **Given:** A tunnel image contains a clear defect that remains stable across pose variants.
  - **When:** The module processes the image.
  - **Then:** The fused mask contains the defect, the uncertainty map is low over the defect core, and the report assigns a risk level based mainly on morphology rather than review uncertainty.

- AE2. Image with inconsistent horizontal or blocky prediction
  - **Covers:** R4, R6, R9, R12
  - **Given:** A weak-class region changes class or location across pose variants.
  - **When:** The module fuses predictions.
  - **Then:** The heatmap marks that region as uncertain, and the report recommends manual review instead of presenting the region as fully reliable.

- AE3. Patent figure generation
  - **Covers:** R10, R13
  - **Given:** A representative test image is selected for documentation.
  - **When:** The module runs on that image.
  - **Then:** It produces a coherent set of visual outputs that can be arranged into a method-flow or effect-comparison figure.

---

## Success Criteria

- S1. The module runs on Windows in the existing project environment without custom compilation.
- S2. The module can process at least the existing five-image inference sample style and save all expected artifacts.
- S3. Where ground-truth labels are available, fused prediction metrics can be compared against the single-pass baseline.
- S4. The generated report explains risk and review suggestions using measurable factors rather than only a final class label.
- S5. The output set is strong enough to support a patent disclosure draft and at least one qualitative results figure.

---

## Scope Boundaries

- v1 does not build a web application, desktop application, or full inspection management platform.
- v1 does not retrain the segmentation model unless later planning decides a small optional experiment is worth adding.
- v1 does not claim real structural safety diagnosis; it provides image-based defect risk grading and review guidance.
- v1 does not require complex topology-loss training or persistent-homology packages.
- v1 does not need to support real-time video streams or multi-date defect growth analysis.

---

## Dependencies and Assumptions

- The existing trained checkpoint remains usable for inference.
- The six-class label convention remains: background, simple, blocky, pipeline, vertical, horizontal.
- The existing evaluation split and available labels are sufficient for basic comparison experiments.
- Risk thresholds can start as configurable heuristics and be adjusted after reviewing representative outputs.
- The patent direction values visible technical effect, interpretability, and reproducibility more than claiming a novel backbone.

---

## Sources and Research

- Project baseline context: `README.md`
- Existing model, evaluation, and visualization entrypoint: `train_resnet50.py`
- Current single-image style inference path: `infer_five_images.py`
- Dataset and label loading conventions: `data_adapter.py`
- Existing evaluation output: `experiments/ResNet50_FCN_6cls_fixed/eval_report.txt`
- Relevant external directions discussed for planning: test-time augmentation for segmentation, uncertainty from prediction disagreement, calibration under augmentation, and skeleton or topology-aware crack measurement.
