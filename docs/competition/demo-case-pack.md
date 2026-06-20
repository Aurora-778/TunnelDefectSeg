# Competition Demo Case Pack

这份 case pack 供答辩、视频录制和老师检查进度使用。它优先展示“有证据、可解释、可复现”的样例，而不是随手挑最好看的截图。

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

## How To Present

### C1. Fixed Fusion Harmed / Selected Recovered

- Shows why fixed fusion can shrink small defects.
- Shows selected mask recovery relative to fused mask.
- Best for explaining the enhancement layer's value.

### C2. Stable Fused / GT Caution

- Shows that fused output is not always rejected.
- Use it to show the method is conservative, not over-reactive.
- Keep the GT caution sentence on the slide.

### C3. High Uncertainty / Error Overlap

- Shows uncertainty as a real review signal.
- Best for the “human-in-the-loop” innovation point.

### C4. Review Queue Top Sample

- Shows the image-ranking logic before any GT-derived evaluation.
- Best for explaining `review_priority`.

### C5. Limitation Case

- Shows the method is not a universal raw mIoU booster.
- Good for honest claim boundaries during Q&A.

### C6. High Disagreement Case

- Pending until a concrete per-image disagreement example is exported.
- Should not be narrated as measured evidence yet.

### C7. Morphology Degradation Case

- Pending until a concrete morphology-degradation example is exported.
- Should not be narrated as measured evidence yet.

## Artifact Templates

The following artifact names are used across the pack:

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

## Boundary Reminder

- `self_consistency` is not ground-truth accuracy.
- `review_priority` is not structural safety diagnosis.
- `simulation-only` spatial outputs must be labelled as such, and prototype outputs should still carry `location_source` / `accuracy_level`.
- `official samples` are not yet integrated into the current pack.
