---
title: Enhancement evidence summaries from evaluation JSON
date: 2026-06-04
last_updated: 2026-06-05
category: best-practices
module: enhancement evidence evaluation
problem_type: best_practice
component: tooling
severity: medium
applies_when:
  - "Adaptive fusion or confidence-risk outputs need dataset-level evidence for a demo, report, or patent note"
  - "Existing evaluation JSON already contains per-sample confusion matrices and adaptive-selection metadata"
  - "Representative examples are selected automatically from aggregate evaluation results"
tags: [enhancement-evidence, adaptive-fusion, evaluation, miou, representative-examples, patent]
---

# Enhancement evidence summaries from evaluation JSON

## Context

The adaptive confidence-risk module originally had strong per-image artifacts: `single_mask`, `fused_mask`, `selected_mask`, uncertainty heatmaps, disagreement maps, morphology metrics, and risk reports. That was enough for a visual demo, but weak for a patent or experiment story because a single image can be dismissed as cherry-picked.

The repository already had full adaptive evaluation outputs under `experiments/adaptive_fusion_eval_test_full.json` and `experiments/adaptive_fusion_eval_all_full.json`. Those files contain per-sample `single`, `fused`, and `selected` confusion matrices plus adaptive-selection metadata, so they can be transformed into dataset-level evidence without rerunning model inference.

## Guidance

When turning segmentation enhancement results into report-ready evidence, prefer a compact summary layer over hand-entered UI constants. The summary should read the existing evaluation JSON and derive:

- Aggregate `single`, `fused`, and `selected` mIoU / mDice.
- `selected_vs_fused_mIoU` and `selected_vs_single_mIoU`.
- Selection-mode counts and rates.
- Foreground shrinkage counts, rates, and total shrunk pixels.
- Small-defect guard counts and protected pixels.
- High-defect-uncertainty review rates.
- Uncertainty-to-error overlap when GT masks are available.
- Class-level IoU comparisons.
- Representative examples for patent figures.

The implementation in this repository is `enhancement_evidence.py`. It reuses confusion matrices already written by `evaluate_confidence_risk.py`; predicted foreground pixels are recovered from prediction columns:

```python
def foreground_pixels(report: dict) -> int:
    matrix = np.asarray(report.get("confusion_matrix", []), dtype=np.int64)
    if matrix.ndim != 2 or matrix.size == 0 or matrix.shape[1] <= 1:
        return 0
    return int(matrix[:, 1:].sum())
```

This matters because the full evaluation JSON does not store raw masks. The confusion matrix still preserves enough information to count predicted pixels per class:

```text
rows = target classes
columns = predicted classes
foreground prediction pixels = sum(columns 1..N)
```

For uncertainty/error evidence, keep the metric names explicit:

- `error_high_uncertainty_fraction`: among wrong pixels, how many were covered by high uncertainty. This is error coverage / recall.
- `high_uncertainty_error_fraction`: among high-uncertainty pixels, how many were actually wrong. This is uncertainty precision.
- `pixel_high_uncertainty_threshold`: the per-pixel cutoff used to mark a pixel as high uncertainty. Current value: `0.35`.
- `review_fraction_threshold`: the per-sample cutoff used to count a sample as needing review based on the fraction of high-uncertainty defect pixels. Current value: `0.5`.

Do not collapse those thresholds into one generic `high_uncertainty_threshold`. They answer different questions: pixel marking vs sample-level review triage.

Generated summaries should be committed when they are small and intentionally used as stage evidence. The current compact outputs are:

- `experiments/enhancement_evidence_test_summary.json`
- `experiments/enhancement_evidence_all_summary.json`

## Why This Matters

The strongest claim for the enhancement module is not that it always beats `single` mIoU. The more defensible claim is:

> Fixed TTA fusion often shrinks defect foreground; adaptive selection detects harmful or unstable fusion and recovers much of the selected-vs-fused loss while also producing uncertainty and morphology review evidence.

The compact summaries make that measurable. After U1, the generated evidence showed:

| Scope | selected vs fused mIoU | Protected events | Successful guard events | Total protected pixels |
|---|---:|---:|---:|---:|
| test 150 images | +0.0141 | 99 / 150 | 72 / 150 | 156,301 px |
| all 1000 images | +0.0393 | 721 / 1000 | 614 / 1000 | 1,230,616 px |

That is much stronger than a single `t1_1` demo because it describes how often the module protects against fixed-fusion shrinkage.

After U3, the uncertainty overlap evidence added a second defensible claim: high-uncertainty regions are useful review targets when labels are available.

| Scope | Error coverage | High-uncertainty error precision | Pixel threshold | Review fraction threshold |
|---|---:|---:|---:|---:|
| test 150 images | 45.66% | 80.66% | 0.35 | 0.50 |
| all 1000 images | 44.54% | 78.27% | 0.35 | 0.50 |

This supports review prioritization, not fake accuracy for unlabeled uploads. For drag-and-drop images without GT, keep showing self-consistency, uncertainty, disagreement, morphology, and selection mode; do not show true mIoU or error-overlap metrics.

Representative-example selection also matters. A bug in the first version selected an empty-background image as `stable_fused` because both `single` and `fused` masks had no foreground, giving `self_foreground_iou = 1.0`. That is mathematically consistent but weak as a demo or patent figure. A stable fused example should prefer a non-empty defect foreground case:

```python
stable_fused_with_defect = _best_example(
    samples,
    evidence_rows,
    lambda item: item["selection_mode"] == "fused" and item["selected_foreground_pixels"] > 0,
    lambda item: (
        item["self_foreground_iou"] or 0.0,
        item["selected_vs_single_mIoU"] or 0.0,
    ),
)
```

Only fall back to an empty-background stable case when no non-empty fused example exists.

## When to Apply

- Apply this pattern when a demo panel, report, or patent note needs dataset-level proof for adaptive enhancement.
- Apply it when existing evaluation JSON includes confusion matrices, deltas, uncertainty summaries, and selection metadata.
- Apply it before wiring aggregate results into the web UI so the UI reads generated evidence instead of hardcoded sample values.
- Apply it when choosing representative examples automatically; empty masks should not silently win over defect-bearing examples.

## Examples

Generate compact evidence from the existing test split:

```powershell
python enhancement_evidence.py experiments/adaptive_fusion_eval_test_full.json --output experiments/enhancement_evidence_test_summary.json
```

Generate compact evidence from all labeled samples:

```powershell
python enhancement_evidence.py experiments/adaptive_fusion_eval_all_full.json --output experiments/enhancement_evidence_all_summary.json
```

New evaluations can also emit the compact evidence directly:

```powershell
python evaluate_confidence_risk.py --split test --output experiments/adaptive_fusion_eval_test_full.json --evidence-output experiments/enhancement_evidence_test_summary.json
```

Lock the threshold vocabulary with focused tests:

```python
result = aggregate_comparisons([comparison])
assert result["uncertainty_error_overlap"]["pixel_high_uncertainty_threshold"] == 0.35

summary = summarize_enhancement_evidence(evaluation, high_uncertainty_threshold=0.5)
assert np.isclose(summary["uncertainty_review"]["pixel_high_uncertainty_threshold"], 0.35)
assert summary["uncertainty_review"]["review_fraction_threshold"] == 0.5
```

The web panel should present both values. `Error coverage` and `HU error precision` use `pixel_high_uncertainty_threshold`; `Review signal` uses `review_fraction_threshold`.

Protect the representative-example behavior with a focused test:

```python
def test_stable_fused_example_prefers_non_empty_defect_over_empty_background():
    empty = _comparison(
        "empty.jpg",
        single=[[0, 0], [0, 0]],
        fused=[[0, 0], [0, 0]],
        selected=[[0, 0], [0, 0]],
        target=[[0, 0], [0, 0]],
        mode="fused",
    )
    defect = _comparison(
        "defect.jpg",
        single=[[0, 1], [0, 1]],
        fused=[[0, 1], [0, 1]],
        selected=[[0, 1], [0, 1]],
        target=[[0, 1], [0, 1]],
        mode="fused",
    )

    result = summarize_enhancement_evidence({"samples": [empty, defect]})

    assert result["representative_examples"]["stable_fused"]["image"] == "defect.jpg"
```

## Related

- `enhancement_evidence.py`
- `evaluate_confidence_risk.py`
- `tests/test_enhancement_evidence.py`
- `experiments/enhancement_evidence_test_summary.json`
- `experiments/enhancement_evidence_all_summary.json`
- `docs/plans/2026-06-04-001-feat-enhancement-evidence-optimization-plan.md`
- `docs/patent-notes/tunnel-defect-confidence-risk.md`
