# Patent Case Pack

## Purpose

This document lists the current representative cases for patent figures, paper figures, PPT explanation, and teacher review. It is generated from the committed patent evidence pack contract, not from hand-picked screenshots.

Source JSON:

- `experiments/patent_evidence_test_pack.json`

Important boundary:

- `artifact_path_templates` show where images should exist after case artifacts are generated.
- `artifact_paths_verified: false` means the current checkout does not yet contain those generated images.
- `gt_available: true` means GT-derived evaluation fields are meaningful for that labelled case.

## Case Manifest

| Case | Category | Status | Image | GT available | Artifact verified | Source |
|---|---|---|---|---|---|---|
| C1 | fixed fusion harmed / selected recovered | measured | `pos_10_t1_30.jpg` | true | false | `representative_examples.small_defect_guard` |
| C2 | model-internal stable fused / GT caution | measured | `pos_10_t4_40.jpg` | true | false | `representative_examples.stable_fused` |
| C3 | high uncertainty / error overlap | measured | `pos_5_t4_21.jpg` | true | false | `representative_examples.high_uncertainty_review` |
| C4 | top review queue sample | measured | `neg_-5_t2_18.jpg` | true | false | `review_queue_summary.top_k[0]` |
| C5 | limitation case | measured | `t4_25.jpg` | true | false | `representative_examples.limitation_case` |
| C6 | high disagreement case | pending | `N/A` | false | false | `per-image report disagreement_summary` |
| C7 | morphology degradation case | pending | `N/A` | false | false | `per-image report morphology_delta.algorithmic_evidence` |

## Case Notes

### C1. Fixed Fusion Harmed / Selected Recovered

Use this case to show fixed fusion shrinkage and selected-mask foreground protection.

- Image: `pos_10_t1_30.jpg`
- Selection mode: `single`
- Fixed fusion harmed mIoU: true
- Selected recovers over fused: true
- Selected vs fixed fused mIoU: `+0.3403`
- Protected pixels vs fused: `1107`
- Artifact template stem: `experiments/confidence_risk/pos_10_t1_30_*`

### C2. Model-Internal Stable Fused / GT Caution

Use this case to show the method does not always reject fixed fusion based on model-internal stability. It should be presented with caution because the GT evaluation still shows a selected-vs-single loss, so this is a conservatism-and-boundary example rather than a positive accuracy-gain example.

- Image: `pos_10_t4_40.jpg`
- Selection mode: `fused`
- Self foreground IoU: `0.9776`
- Selected vs fixed fused mIoU: `0.0000`
- Selected vs single mIoU: `-0.0776`
- Artifact template stem: `experiments/confidence_risk/pos_10_t4_40_*`

### C3. High Uncertainty / Error Overlap

Use this case to show uncertainty as a GT-evaluated review signal on labelled samples.

- Image: `pos_5_t4_21.jpg`
- Defect high-uncertainty fraction: `0.9978`
- Error high-uncertainty fraction: `0.9765`
- HU error precision: `0.9052`
- Selected vs fixed fused mIoU: `+0.0491`
- Artifact template stem: `experiments/confidence_risk/pos_5_t4_21_*`

### C4. Top Review Queue Sample

Use this case to show model-internal review ranking before GT-derived bucket evaluation.

- Image: `neg_-5_t2_18.jpg`
- Priority: `high`
- Score: `4.50`
- Reasons:
  - defect high-uncertainty fraction `0.980`
  - single/fused foreground IoU `0.340` is severely unstable
  - fused foreground shrink fraction `0.654`
  - selected preserves `2523` pixels versus fused
  - adaptive selection chose single instead of fixed fused output
- Artifact template stem: `experiments/confidence_risk/neg_-5_t2_18_*`

### C5. Limitation Case

Use this case to state that selected is not guaranteed to beat single-pass output.

- Image: `t4_25.jpg`
- Selection mode: `fused`
- Selected vs single mIoU: `-0.0838`
- Selected vs fixed fused mIoU: `0.0000`
- Boundary message: the method should be claimed as fixed-fusion recovery and review prioritisation, not universal raw mIoU improvement over single.
- Artifact template stem: `experiments/confidence_risk/t4_25_*`

### C6. High Disagreement Case

Status: pending.

This case should be selected after generated per-image reports include deterministic `disagreement_summary` evidence for representative-case selection. It should not be shown as measured until a concrete image and artifact manifest exist.

### C7. Morphology Degradation Case

Status: pending.

This case should be selected after generated per-image reports include `morphology_delta.algorithmic_evidence`, such as `area_shrinkage`, `component_fragmentation`, `skeleton_shortening`, or `direction_shift`. It should not be shown as measured until a concrete image and artifact manifest exist.

## Artifact Contract

The current evidence pack stores path templates for these artifact types:

- `_single_mask.png`
- `_fused_mask.png`
- `_hybrid_mask.png`
- `_selected_mask.png`
- `_overlay.png`
- `_selected_overlay.png`
- `_uncertainty_heatmap.png`
- `_disagreement_heatmap.png`
- `_skeleton.png`
- `_report.json`

For the current checkout, `artifact_paths_verified` is false for the measured cases. This means the cases are valid evidence rows, but the visual artifact files still need to be generated before screenshots or patent figures can be assembled.

## Recommended Figure Usage

- Patent effect comparison: C1.
- Method conservatism with GT caution: C2.
- Uncertainty review signal: C3.
- Review queue / triage explanation: C4.
- Limitation and claim boundary: C5.
- Future additions after artifact generation: C6 and C7.
