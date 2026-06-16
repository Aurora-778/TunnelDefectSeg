# Claim To Evidence Matrix

## Purpose

This matrix maps each paper/patent claim to evidence sources, current status, and limitations. It is an integrity gate: claims without evidence must remain `pending` and cannot be written as proven results.

## Matrix

| Claim ID | Claim | Status | Evidence source | Limitation / boundary |
|---|---|---|---|---|
| C1 | The project uses SegFormer B1 as a stronger 6-class tunnel defect mask source. | supported | `docs/experiments/enhancement-evidence-summary.md`; final training log values copied into README | Backbone evidence only, not enhancement gain. |
| C2 | Fixed fusion can reduce or harm defect foreground on labeled samples. | supported | `experiments/enhancement_evidence_test_summary.json`; `experiments/enhancement_evidence_all_summary.json` | GT-required aggregate evidence. |
| C3 | Adaptive selected mask recovers mIoU relative to fixed fused output. | supported | `docs/experiments/patent-ablation-summary.md`; `experiments/patent_evidence_test_pack.json` | Does not imply selected universally beats single. |
| C4 | Adaptive selection protects foreground pixels relative to fixed fused output. | supported | `small_defect_guard` in evidence summaries; C1 in `docs/experiments/patent-case-pack.md` | Depends on current thresholds and labeled evaluation. |
| C5 | Uncertainty is useful as a review signal on labeled data. | supported | `uncertainty_review` in enhancement summaries; C3 in `docs/experiments/patent-case-pack.md` | Error overlap and calibration require GT. |
| C6 | Review priority ranks samples using model-internal evidence before GT evaluation. | supported | `review_queue_summary` in `experiments/patent_evidence_test_pack.json`; C4 in `docs/experiments/patent-case-pack.md` | GT-derived flags evaluate buckets after ranking, not during ranking. |
| C7 | Morphology_delta provides structured algorithmic evidence. | supported | `morphology_adapter.py`; `tests/test_morphology_adapter.py`; `docs/patent-notes/confidence-review-disclosure.md` | Representative morphology-degradation case artifact is pending. |
| C8 | The method can produce no-GT review signals for uploaded images. | supported | README; `run_confidence_risk.py`; `tests/test_confidence_risk_outputs.py` | No true mIoU, error overlap, or GT calibration without labels. |
| C9 | The method is suitable for patent disclosure as a post-inference confidence review chain. | supported | `docs/patent-notes/confidence-review-disclosure.md`; `docs/patent-notes/confidence-review-claims-draft.md` | Formal patent filing still requires prior-art search and attorney review. |
| C10 | Removing morphology_delta would reduce review-priority quality. | pending | no generated ablation JSON yet | Must remain pending until `selected_without_morphology` or `review_priority_without_morphology_delta` is evaluated. |
| C11 | Removing uncertainty/disagreement would reduce review-priority quality. | pending | no generated ablation JSON yet | Must remain pending until `selected_without_uncertainty_disagreement` is evaluated. |
| C12 | High-disagreement and morphology-degradation visual cases are available as generated artifacts. | pending | C6/C7 in `docs/experiments/patent-case-pack.md` | Current checkout has pending case rows; do not present as measured figures. |

## Evidence Source Rules

- Use committed JSON, committed docs, test files, or reproducible scripts as evidence.
- Mark a claim as `pending` if the supporting JSON or artifact does not exist.
- Do not cite Web screenshots as the only evidence for algorithm claims.
- Do not reuse SegFormer backbone `mIoU 84.33` as proof of post-inference enhancement.
- Do not describe no-GT upload results as true accuracy.

## Claims Ready For Teacher Presentation

The following claims are currently safe to present:

- C1 through C9, with their limitations stated.

The following claims need more work:

- C10, C11, C12.

## Claims Ready For Patent Drafting

Safe drafting focus:

- Candidate mask comparison.
- Fixed fusion harm detection.
- Adaptive selected-mask choice.
- Uncertainty/disagreement review evidence.
- Morphology_delta structured evidence.
- Review priority and review queue.

Do not draft as independent novelty:

- SegFormer network.
- TTA itself.
- entropy itself.
- skeletonization itself.
- Web drag-and-drop UI.
