# Competition Experiment Summary

这份材料把当前项目的实验结果改写成比赛答辩口径，重点不是单个模型分数，而是“多领域巡检 + 空间定位 + 可信复核 + 报告闭环”。

## Evidence Sources

- Backbone logs: `SegFormer B1 6-class` final evaluation at `160000 iter`
- Enhancement summary: `experiments/enhancement_evidence_test_summary.json`
- Full labeled evidence: `experiments/enhancement_evidence_all_summary.json`
- Competition evidence pack: `experiments/patent_evidence_test_pack.json`

## Data Status

| Layer | Current status | Notes |
|---|---|---|
| Internal labeled dataset | Measured | Current backbone and enhancement metrics all come from the internal train/val/test splits. |
| Demo cases | Measured + pending | C1-C5 are measured evidence cases; C6-C7 are pending future additions. |
| Official competition samples | Planned | Not yet integrated into the current repo evidence pack. |
| Simulation / calibration | Planned / prototype evidence only | The current main evidence pack does not include spatial-mapping artifacts yet; any future simulation or calibration output must carry explicit source labels. |

## Backbone Result

| Model | Iter | mIoU | mAcc | aAcc |
|---|---:|---:|---:|---:|
| SegFormer B1 6-class | 160000 | 84.33 | 91.17 | 98.62 |

Per-class IoU snapshot:

| Class | IoU | Comment |
|---|---:|---|
| background | 99.01 | Very strong background separation. |
| simple | 80.23 | Good baseline defect class. |
| blocky | 53.58 | Main bottleneck class. |
| pipeline | 94.16 | Strong and stable. |
| vertical | 95.85 | Strong and stable. |
| horizontal | 83.17 | Good coverage. |

## Enhancement Result

| Scope | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fused |
|---|---:|---:|---:|---:|---:|
| Test split | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 |
| All labeled samples | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 |

Interpretation:

- Adaptive selection consistently recovers accuracy relative to fixed fusion.
- It does not always beat single-pass output, so the claim should be recovery and review support, not universal accuracy dominance.

## Review Evidence

| Scope | High-uncertainty review signals | Error coverage | HU error precision |
|---|---:|---:|---:|
| Test split | 61 / 150 | 45.66% | 80.66% |
| All labeled samples | 431 / 1000 | 44.54% | 78.27% |

Interpretation:

- `pixel_high_uncertainty_threshold = 0.35`
- `review_fraction_threshold = 0.50`
- The system should present uncertainty as a review signal, not as a safety diagnosis.

## Competition Claim Frame

1. The backbone provides a strong six-class SegFormer mask source.
2. The enhancement layer detects when fixed fusion harms small or unstable defects.
3. The review queue ranks images by model-internal evidence and is suitable for human triage.
4. Spatial mapping should be presented as planned or prototype-only until concrete calibrated or simulation artifacts are included.

## Suggested Slide Narrative

- First show backbone accuracy and the defect class bottleneck.
- Then show selected-vs-fused recovery on test/all labeled data.
- Then show review queue and uncertainty overlap as the human-in-the-loop innovation.
- Finally show report export as the current system-level engineering closure, and present spatial mapping as the next competition-oriented extension.
