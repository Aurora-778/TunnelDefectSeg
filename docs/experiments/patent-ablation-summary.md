# Patent Ablation Evidence Summary

## Purpose

This document turns the current confidence-review evidence into a patent-oriented ablation matrix. It separates measured variants from pending variants so the project can discuss algorithm contribution without inventing experiment numbers.

The core claim is not that `selected mask` universally beats `single mask`. The measured claim is narrower and stronger:

> The confidence-review method detects harmful fixed fusion, recovers much of the fixed-fused loss, preserves small-defect foreground pixels, and ranks unstable samples for manual review.

## Evidence Sources

| Source | Role | GT required |
|---|---|---|
| `experiments/enhancement_evidence_test_summary.json` | test split aggregate evidence, 150 samples | yes |
| `experiments/enhancement_evidence_all_summary.json` | all labeled samples aggregate evidence, 1000 samples | yes |
| `experiments/patent_evidence_test_pack.json` | compact patent pack with backbone/enhancement boundaries and representative cases | mixed |
| `docs/experiments/enhancement-evidence-summary.md` | narrative summary of current stage evidence | mixed |

## Variant Matrix

| Variant | Status | What it means | Primary output | Evidence source |
|---|---|---|---|---|
| `single` | measured | single-pass model prediction without fixed fusion | `single mask` | enhancement evidence JSON |
| `fixed_fused` | measured | fixed TTA/probability fusion output | `fused mask` | enhancement evidence JSON |
| `selected` | measured | adaptive choice among candidate masks | `selected mask` | enhancement evidence JSON |
| `selected_without_morphology` | pending | adaptive selection without morphology_delta or morphology-derived review evidence | pending | not yet generated |
| `full_review_priority` | measured as review evidence | selected mask plus uncertainty, disagreement, self-consistency, shrinkage, protected pixels, morphology_delta and review priority | review queue / priority buckets | enhancement evidence JSON and patent evidence pack |

## Measured mIoU Comparison

These values require GT masks and should only be used for labeled split analysis.

| Scope | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fixed fused | Selected vs single | Source |
|---|---:|---:|---:|---:|---:|---:|---|
| Test split | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 | +0.0001 | `experiments/enhancement_evidence_test_summary.json` |
| All labeled samples | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 | -0.0044 | `experiments/enhancement_evidence_all_summary.json` |

Interpretation: `selected` recovers the loss introduced by `fixed_fused`. On all labeled samples it remains slightly below `single`, so the patent/report wording should emphasize recovery from harmful fusion and review evidence, not universal mIoU improvement over single-pass prediction.

## Fixed Fusion Harm And Small-Defect Guard

| Scope | Fixed fusion harmed | Selected recovered | Protected events | Successful guard events | Protected pixels vs fused |
|---|---:|---:|---:|---:|---:|
| Test split | 99 / 150 | 81 / 150 | 99 / 150 | 72 / 150 | 156,301 |
| All labeled samples | 817 / 1000 | 699 / 1000 | 721 / 1000 | 614 / 1000 | 1,230,616 |

Interpretation: this is the strongest current evidence for the adaptive selection module. The system identifies cases where fixed fusion suppresses defect foreground and preserves pixels that would otherwise be lost.

## Review Priority Ablation Evidence

`full_review_priority` is not a new mask mIoU variant. It is the review triage layer built from model-internal evidence. Ranking uses no GT; GT-derived flags are used only after ranking to evaluate whether buckets concentrate real errors or fixed-fusion harm.

| Scope | Review signal | GT usage | Current evidence |
|---|---|---|---|
| Test split | uncertainty, self-consistency, shrinkage, protected pixels, selection mode, morphology_delta | evaluation only | `review_queue_summary` supported in `experiments/enhancement_evidence_test_summary.json` |
| Patent pack | same as above, plus artifact contract and claim boundaries | mixed | `review_queue_summary` included in `experiments/patent_evidence_test_pack.json` |

The current test summary records high/medium/low/none priority buckets. Use `top_k` for representative review cases and `bucket_metrics` for report language about whether high-priority buckets concentrate fixed fusion harm or selected recovery.

## Pending Ablations

| Pending variant | Why it matters | Required implementation |
|---|---|---|
| `selected_without_morphology` | separates morphology_delta as algorithmic evidence from mask-selection evidence | add an evaluation switch that disables morphology-derived score/report inputs while preserving single/fused/selected outputs |
| `selected_without_uncertainty_disagreement` | measures how much review priority depends on probability uncertainty and TTA disagreement | add an evaluation switch that keeps mask outputs but removes uncertainty/disagreement from priority scoring |
| `review_priority_without_morphology_delta` | tests whether morphology evidence improves review ranking or only helps explanation | export bucket metrics with morphology_delta disabled |

These rows are intentionally marked pending. They should not be described as measured results until a future evaluation writes JSON outputs for each variant.

## Claim-Safe Reporting Language

Use:

- "selected recovers fixed-fused mIoU loss"
- "fixed fusion harmed many labeled samples"
- "adaptive selection protects foreground pixels relative to fixed fusion"
- "review priority ranks samples using model-internal evidence, and GT is used afterward to evaluate the queue"
- "morphology_delta provides structured evidence flags for area shrinkage, fragmentation, skeleton change and direction shift"

Avoid:

- "selected always beats single"
- "review priority proves structural danger"
- "uncertainty is ground-truth accuracy"
- "SegFormer is the invented method"
- "pending ablations are already measured"

## Next Evidence Upgrade

The next measurable improvement is to add evaluation switches for the pending ablations and export their summaries beside the current measured JSON files. Until then, this document should be treated as the honest current ablation matrix.
