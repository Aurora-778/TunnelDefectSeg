# Enhancement Evidence Experiment Summary

## Purpose

This document collects the current stage evidence for the tunnel defect confidence-risk enhancement module. It is intended for project reporting, patent disclosure drafting, and demo narration.

The evidence is separated into two layers:

- **Backbone segmentation quality:** SegFormer B1 validates the raw model quality after training.
- **Post-inference enhancement quality:** adaptive fusion, uncertainty, morphology, and review signals validate the reliability layer around a mask provider.

Do not mix the two claims. SegFormer improves the mask source; the enhancement module makes output selection and review evidence more inspectable. Current SegFormer confidence-risk inference uses same-checkpoint identity/hflip probability maps, so `fused_mask`, uncertainty, and disagreement can be traced to the same model run.

## Backbone Evidence

The trained SegFormer B1 run reached the following final logged evaluation:

| Backbone | Iter | mIoU | mAcc | aAcc |
|---|---:|---:|---:|---:|
| SegFormer B1 6-class | 160000 | 84.33 | 91.17 | 98.62 |

Per-class IoU at the same final evaluation:

| Class | IoU | Acc |
|---|---:|---:|
| background | 99.01 | 99.51 |
| simple | 80.23 | 88.40 |
| blocky | 53.58 | 75.56 |
| pipeline | 94.16 | 97.43 |
| vertical | 95.85 | 97.75 |
| horizontal | 83.17 | 88.34 |

Interpretation: `blocky` remains the bottleneck class, while `pipeline`, `vertical`, and `horizontal` are already strong enough to support a higher-quality backbone story.

## Adaptive Enhancement Evidence

The following tables come from:

- `experiments/enhancement_evidence_test_summary.json`
- `experiments/enhancement_evidence_all_summary.json`

### mIoU Recovery From Fixed Fusion

| Scope | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fused | Selected vs single |
|---|---:|---:|---:|---:|---:|---:|
| Test split | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 | +0.0001 |
| All labeled samples | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 | -0.0044 |

Interpretation: the adaptive module consistently recovers accuracy relative to fixed TTA fusion. It should not be claimed as universally better than the single-pass baseline, especially on the all-sample aggregate.

### Small-Defect Guard

| Scope | Protected events | Successful guard events | Fixed fusion harmed | Selected recovered | Protected pixels vs fused |
|---|---:|---:|---:|---:|---:|
| Test split | 99 / 150 | 72 / 150 | 99 / 150 | 81 / 150 | 156,301 |
| All labeled samples | 721 / 1000 | 614 / 1000 | 817 / 1000 | 699 / 1000 | 1,230,616 |

Interpretation: fixed fusion often shrinks defect foreground. Adaptive selection detects unstable or harmful fusion and preserves defect evidence in many cases.

### Selection Modes

| Scope | Single selected | Fused selected |
|---|---:|---:|
| Test split | 122 / 150 | 28 / 150 |
| All labeled samples | 856 / 1000 | 144 / 1000 |

Interpretation: the current policy is conservative. That is acceptable for the patent/report story if the claim is harmful-fusion detection and review support rather than "always fuse."

### Uncertainty Review Evidence

| Scope | Error coverage | HU error precision | Pixel high-uncertainty threshold | Review fraction threshold | Review signal count |
|---|---:|---:|---:|---:|---:|
| Test split | 45.66% | 80.66% | 0.35 | 0.50 | 61 / 150 |
| All labeled samples | 44.54% | 78.27% | 0.35 | 0.50 | 431 / 1000 |

Definitions:

- `Error coverage`: among prediction-error pixels, the fraction covered by high uncertainty.
- `HU error precision`: among high-uncertainty pixels, the fraction that are prediction errors.
- `pixel_high_uncertainty_threshold`: per-pixel threshold used to mark high uncertainty.
- `review_fraction_threshold`: per-sample threshold used to count images whose defect region has enough high-uncertainty pixels to trigger review.

Interpretation: high-uncertainty regions are meaningful review targets when GT masks exist. For drag-and-drop images without GT, the system must not display true mIoU or error-overlap metrics; it should display self-consistency, uncertainty, disagreement, morphology, and selected-mask rationale.

### U6 Calibration And Review Priority

U6 extends the report contract with two reliability additions:

- `uncertainty_calibration`: available only with GT masks. It bins uncertainty values and compares each bin's mean uncertainty with the actual pixel error rate, producing an ECE-like calibration gap.
- `review_priority`: available for every processed image. It combines image risk, defect uncertainty, disagreement, single/fused self-consistency, foreground shrinkage, and selected-mask reasons into a manual-review priority.

Interpretation: calibration evidence answers whether uncertainty is a trustworthy review signal on labeled splits. Review priority answers which image should be inspected first in Web or field-review workflows. Neither should be described as structural safety diagnosis or final maintenance decision-making.

## Representative Cases To Use In Slides Or Patent Figures

Use examples from the generated evidence JSON instead of hand-picking images:

- `small_defect_guard`: demonstrates protected foreground pixels and selected-vs-fused recovery.
- `stable_fused`: demonstrates when fused output is accepted. Prefer non-empty defect foreground cases.
- `high_uncertainty_review`: demonstrates uncertainty-based review prioritization.
- `limitation_case`: demonstrates honest boundaries where selected output may not beat single-pass output.

The same evaluation JSON can now be wrapped into a patent-ready evidence pack. The pack keeps backbone evidence separate from post-inference enhancement evidence, attaches artifact path templates plus current-repo availability flags to representative cases, and carries the GT/no-GT claim boundary used by the Web demo and patent notes:

```powershell
python enhancement_evidence.py experiments/adaptive_fusion_eval_test_full.json --output experiments/enhancement_evidence_test_summary.json --patent-pack-output experiments/patent_evidence_test_pack.json
```

The committed pack is intentionally compact. Its artifact fields are templates unless `artifact_exists` and `artifact_paths_verified` say the corresponding images are present in the current checkout.

Use the pack when preparing patent figures or teacher-review materials, because it preserves deterministic case selection instead of relying on hand-picked screenshots.

The current case manifest for patent/PPT figures is summarized in `docs/experiments/patent-case-pack.md`. It distinguishes measured cases from pending disagreement or morphology-degradation cases and records whether artifact paths are verified in the current checkout.

### Review Queue Evidence

The compact summary also includes `review_queue_summary`. The queue is ranked from model-internal evidence only: uncertainty, single/fused self-consistency, foreground shrinkage, protected pixels, and selected-mask mode. GT-derived flags are used only after ranking to evaluate whether high-priority buckets cover real error, fixed-fusion harm, or selected recovery events.

On the current test summary, the queue is supported and produces high/medium/low/none buckets. Use `top_k` for demo case browsing, and use `bucket_metrics` for patent or report language about whether review priority concentrates problematic samples.

For a patent-oriented ablation matrix, see `docs/experiments/patent-ablation-summary.md`. That document marks `selected_without_morphology` and related ablations as pending when no generated JSON exists, instead of filling in estimated values.

## Claim Boundaries

- Do not claim the enhancement module invents SegFormer, TTA, entropy uncertainty, or skeletonization.
- Do not claim selected output always beats single-pass mIoU.
- Do not compute or display true mIoU for upload-only images without GT masks.
- Do not present image-based review priority as structural safety diagnosis or final maintenance decision-making.
- Do not mix legacy heatmaps with SegFormer masks in the Web or slide examples; same-source artifacts are part of the evidence contract.

## Suggested Reporting Sentence

The trained SegFormer B1 backbone provides stronger raw six-class tunnel defect segmentation, while the confidence-risk enhancement module adds model-agnostic adaptive output selection, uncertainty/error review evidence, morphology quantification, and human-review prioritization. On all 1000 labeled samples, adaptive selection improves selected-vs-fixed-fused mIoU by +0.0393 and preserves 1,230,616 foreground pixels relative to fixed fusion; high-uncertainty regions contain prediction errors with 78.27% precision under the current pixel threshold of 0.35.
