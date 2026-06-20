---
title: Project Next Optimization Requirements
date: 2026-06-20
status: draft
origin: user asked what the tunnel defect project should optimize next
---

# Project Next Optimization Requirements

## Goal

Identify the next high-leverage optimization direction for the tunnel defect segmentation and confidence-review project. The goal is to improve value for teacher review, competition submission, patent disclosure, and later paper-style work without inventing unsupported claims.

## What I Already Know

- The project already has a trained SegFormer B1 mask source with final mIoU 84.33%.
- The confidence-review chain already includes selected mask, uncertainty, disagreement, morphology evidence, review priority, Web demo, PPT, and competition docs.
- The strongest current story is: fixed fusion can harm small foreground defects, and adaptive selection can reduce that harm.
- The weakest SegFormer class is `blocky`, with IoU 53.58%.
- The academic review report marks ablations, sensitivity analysis, citation-backed related work, C6/C7 visual artifacts, and external validity as the main missing pieces.
- The competition plan has already started B+C: multidomain schema plus spatial mapping, but track/equipment remain demo adapters and spatial mapping remains planned/prototype.

## Candidate Directions

### Direction A: Review-Priority Ablation And Sensitivity Pack

Build measured evidence for whether the review-priority module adds value beyond simpler baselines.

- Compare full review priority against uncertainty-only, shrinkage-only, self-IoU-only, and without-morphology variants.
- Export JSON tables and documentation so patent/paper claims become supported instead of pending.
- Add threshold sensitivity over uncertainty, review fraction, self-consistency, and shrinkage thresholds.

Why it matters:

- Directly addresses the academic review's biggest weakness.
- Strengthens patent claims around the algorithm chain.
- Does not require new labels or official competition samples.

### Direction B: Blocky-Class Improvement Track

Focus on the current weakest segmentation category.

- Add blocky-specific error analysis.
- Generate hard-case slices and confusion summaries.
- Try low-risk training improvements such as class-balanced sampling, loss weighting, or targeted augmentation.

Why it matters:

- Improves the most visible model weakness.
- Makes the final model story stronger if mIoU or blocky IoU improves.
- Requires more training time and has less guaranteed short-term success.

### Direction C: Competition Spatial Mapping Prototype

Turn planned/prototype spatial mapping into a runnable module with clear simulation boundaries.

- Map mask centroid/region to clock position, ring number, mileage, and optional local 3D coordinates.
- Add `location_source` and `accuracy_level` fields.
- Surface the result in report JSON and Web competition view.

Why it matters:

- Matches the competition requirement more directly than another Web polish pass.
- Creates a clearer "multi-domain intelligent inspection" story.
- Must be carefully labelled as simulation/calibration unless real sensors are available.

### Direction D: Case-Pack Completion For C6/C7

Complete the pending visual artifacts for high disagreement and morphology degradation cases.

- Search existing evaluation JSON for representative high-disagreement and morphology-degradation samples.
- Export artifacts and update `docs/experiments/patent-case-pack.md` plus competition case pack.
- Use the generated cases in PPT and patent figures.

Why it matters:

- Fast and concrete.
- Improves teacher/demo confidence quickly.
- Supports visuals but does not by itself prove algorithmic superiority.

## Recommended MVP

Recommended next step: Direction A + Direction D as one compact evidence sprint.

Rationale:

- Direction A turns pending algorithm claims into measured evidence.
- Direction D provides the visual examples needed for teacher presentation, patent figures, and paper narrative.
- Together they are more research/patent-oriented than Web polish, and less risky than immediately retraining blocky.

## Expansion Sweep

Future evolution:

- If ablations show the full priority score is meaningfully better, it can become a paper/patent core result.
- If ablations are weak, the honest pivot is to position morphology and review priority as explainability/reporting rather than accuracy improvement.

Related scenarios:

- Competition materials should reuse the same evidence pack but label results as internal dataset unless official samples are added.
- Web and PPT should only promote measured results that exist in JSON/docs.

Failure and edge cases:

- Some ablations may not outperform simple baselines; that is still useful because it prevents overclaiming.
- Threshold sensitivity may show instability; then the next work should be calibration or simpler scoring.
- Generated cases must verify artifact paths, otherwise docs should keep them marked as pending.

## Open Questions

- Which direction should become the next implementation plan: A+D evidence sprint, B blocky training improvement, C spatial mapping prototype, or another direction?

## Requirements For The Recommended Evidence Sprint

- R1. Add evaluation switches or scripts for review-priority baselines.
- R2. Export ablation metrics into machine-readable JSON.
- R3. Summarize the ablation and sensitivity results in docs without inventing unsupported numbers.
- R4. Generate or identify C6 high-disagreement and C7 morphology-degradation examples.
- R5. Update claim/evidence docs so pending claims become supported only when artifacts exist.

## Acceptance Criteria

- [ ] Ablation outputs include full priority, uncertainty-only, shrinkage-only, self-IoU-only, and without-morphology variants.
- [ ] Sensitivity table exists for the key thresholds.
- [ ] C6/C7 rows have verified artifact paths or remain explicitly pending.
- [ ] Claim-to-evidence matrix no longer overstates pending items.
- [ ] Tests cover the new artifact/document contracts.

## Out Of Scope

- Training a new SegFormer checkpoint.
- Claiming external generalization to other tunnel datasets.
- Claiming structural safety diagnosis or automatic maintenance decisions.
- Replacing track/equipment demo adapters with trained models.

## Technical Notes

- Current plan: `docs/plans/2026-06-18-001-feat-metro-multidomain-inspection-plan.md`
- Academic critique: `docs/paper/academic-review-report.md`
- Current pending evidence: `docs/experiments/patent-ablation-summary.md`, `docs/experiments/patent-case-pack.md`
- Competition pending items: `docs/competition/cs-202613-requirements-matrix.md`, `docs/competition/demo-case-pack.md`
