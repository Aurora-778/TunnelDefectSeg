# Academic Review Report

Review date: 2026-06-16

Reviewed materials:

- `docs/paper/confidence-review-outline.md`
- `docs/paper/claim-to-evidence-matrix.md`
- `docs/experiments/enhancement-evidence-summary.md`
- `docs/experiments/patent-ablation-summary.md`
- `docs/experiments/patent-case-pack.md`
- `docs/patent-notes/confidence-review-disclosure.md`

## Manuscript Profile

Working title: 面向隧道病害分割结果的可信复核方法研究

Field: computer vision for infrastructure inspection; semantic segmentation; uncertainty-aware human review.

Paper maturity: pre-manuscript outline with measured internal evidence. It is stronger than a pure idea note, but not yet a submission-ready paper.

Suggested decision: Major Revision.

Reason: the central contribution is promising and the evidence is honestly bounded, but the manuscript still needs a formal related-work position, reproducible experiment protocol, completed ablations, and visual artifacts before it can support paper-level claims.

## Reviewer Configuration

| Role | Identity | Main focus |
|---|---|---|
| EIC | Applied computer vision / infrastructure inspection editor | contribution, venue fit, claim discipline |
| Reviewer 1 | Methodology and evaluation reviewer | experimental design, reproducibility, ablations |
| Reviewer 2 | Domain reviewer | tunnel defect inspection, segmentation evidence, literature fit |
| Reviewer 3 | Human-in-the-loop / reliability reviewer | practical review workflow, explainability, field use |
| Devil's Advocate | Critical argument reviewer | strongest counterarguments, overclaim risk |

## EIC Review

Recommendation: Major Revision

Confidence: 4/5

The project has a clear and useful positioning: it does not claim to invent SegFormer, TTA, entropy, skeletonization, or mIoU. Instead, it frames the contribution as a post-inference confidence review chain for tunnel defect segmentation outputs. This is the right scientific angle because the current data show a real phenomenon: fixed fusion can harm foreground masks, and adaptive selection can recover part of that loss.

Main strength: the claim-to-evidence discipline is unusually good for an early-stage project. The outline explicitly separates backbone quality from enhancement quality, and it marks pending ablations rather than filling in estimated numbers.

Main weakness: the current text is still closer to a technical report and patent preparation note than a paper. The paper needs a sharper research gap, a formal comparison with related work, and a results section that distinguishes mask-quality results from review-triage results.

Required revision:

1. Add a compact introduction claim such as: "we study when post-inference fusion harms small tunnel-defect masks and how model-internal signals can triage outputs for human review."
2. Convert the Related Work section from topic bullets into a checked citation map.
3. Add a submission-facing experiment protocol: dataset split, preprocessing, model checkpoint, TTA settings, thresholds, metrics, and reproducibility commands.
4. Keep the current conservative boundary language; it is a strength, not a weakness.

## Reviewer 1: Methodology

Recommendation: Major Revision

Confidence: 4/5

### Strengths

S1. The paper separates GT-required metrics from no-GT signals. This avoids the common error of reporting true mIoU for user-uploaded images.

S2. The mIoU comparison is framed correctly: selected recovers loss relative to fixed fused, but does not universally beat single.

S3. The evidence matrix creates a useful audit trail from claims to JSON/docs/tests.

### Major Weaknesses

W1. Pending ablations block the strongest algorithmic claim.

Problem: C10 and C11 are still pending, and `patent-ablation-summary.md` does not yet include measured `selected_without_morphology`, `selected_without_uncertainty_disagreement`, or `review_priority_without_morphology_delta`.

Why it matters: without these ablations, the paper can claim the full chain works, but cannot isolate which part of the chain causes the review-priority improvement.

Suggestion: implement ablation switches and report ranking metrics for review priority, not just mask mIoU. Useful metrics include top-k fixed-fusion-harm coverage, high-priority bucket precision, and selected-recovery coverage.

Severity: Critical for paper submission; acceptable for a progress report.

W2. Review priority needs sensitivity analysis.

Problem: `review_priority` is an explainable additive score, but the paper has not shown whether conclusions depend heavily on one threshold or weight.

Why it matters: reviewers may challenge the score as heuristic unless threshold robustness is shown.

Suggestion: add a small sensitivity table over high-uncertainty threshold, self-IoU threshold, and shrinkage threshold. The goal is not to find perfect weights, but to show the queue behaviour is stable.

Severity: Major.

W3. Calibration language must remain conditional.

Problem: the outline lists uncertainty calibration as a GT-required metric. The current presentation should not imply that calibration has been fully demonstrated unless the binned calibration table exists for the cited experiment.

Why it matters: uncertainty/error overlap and uncertainty calibration are different claims. High HU error precision supports review usefulness; calibration requires a bin-wise uncertainty-vs-error-rate analysis.

Suggestion: word the current result as "uncertainty/error overlap evidence is supported; calibration is available only where the generated calibration bins are present."

Severity: Major.

## Reviewer 2: Domain and Literature

Recommendation: Major Revision

Confidence: 3/5

The domain story is credible: tunnel defect segmentation is a practical inspection problem, and the project focuses on the reliability of segmentation outputs rather than only raw segmentation accuracy. The `blocky` class bottleneck is also useful because it shows the paper is not hiding weak classes.

The main missing piece is literature integration. The current Related Work section lists correct topic areas but intentionally avoids citations. That is safe, but it means the manuscript cannot yet claim a research gap in publishable form.

Required revision:

1. Build a related-work table with columns: topic, representative papers, what they solve, what they do not solve, relation to this work.
2. Make sure the gap is specific: "few works evaluate harmful fixed TTA fusion and convert disagreement/morphology signals into review priority for tunnel defect masks."
3. Avoid claiming "few works" until the search is done. Before search, use "the current project targets the following gap" rather than "no one has done this."
4. Add a domain-validity section explaining whether the dataset covers different tunnel surfaces, lighting, camera distance, and defect types.

## Reviewer 3: Practical Reliability and Human Review

Recommendation: Minor-to-Major Revision

Confidence: 4/5

The human-review angle is one of the strongest parts of the project. The no-GT boundary is clear, and `review priority` is positioned as triage rather than structural safety diagnosis. This makes the system defensible for teacher demonstration and later software copyright.

The paper should make the review workflow more concrete:

1. Who uses the queue: annotator, engineer, inspection operator, or researcher?
2. What action follows a high-priority result: relabel, re-run model, manual check, field reinspection, or just mark uncertain?
3. What is the expected benefit: fewer missed unstable masks, faster case selection, better annotation QA, or safer reporting?

The system has enough evidence to claim "review assistance" but not enough to claim "maintenance decision support." Keep that boundary.

## Devil's Advocate Review

Recommendation: Major Revision

Confidence: 4/5

Strongest counterargument:

The method may be seen as a carefully engineered post-processing and reporting wrapper around standard segmentation outputs rather than a new scientific method. SegFormer, TTA, entropy uncertainty, disagreement maps, skeletonization, and human review queues are all known ideas. The manuscript must therefore prove that the specific combination solves a real failure mode that standard reporting misses. The strongest current evidence is fixed-fusion harm and selected recovery. The weakest point is the review-priority formula. If it is mostly heuristic and not compared with simpler baselines, reviewers may ask why the queue is not simply "sort by uncertainty" or "sort by foreground shrinkage."

Critical issues:

1. Missing baseline for review priority. Compare full priority against at least uncertainty-only, shrinkage-only, and self-IoU-only ranking.
2. Missing ablation for morphology_delta. If morphology is presented as an algorithmic contribution, it needs measured influence or a clearly limited role as explainability.
3. Missing external validity. Results currently come from one dataset/workspace; the paper should avoid broad claims about tunnel inspection in general.

Ignored alternative explanations:

- Fixed fusion may harm because the current TTA set is too small or poorly chosen, not because fusion is generally risky.
- Selected may appear strong because the policy often falls back to single, so the real gain may be harm avoidance rather than better segmentation.
- High uncertainty may correlate with errors because both occur on boundaries, not necessarily because uncertainty is calibrated.

These are manageable if the paper states them directly.

## Editorial Synthesis

Overall decision: Major Revision.

The project is viable for a paper-style technical report and could become a workshop/conference-style paper after completing ablations and literature positioning. It is not yet ready as a full submission because several contribution claims are still supported by internal engineering evidence rather than paper-grade comparative experiments.

### Highest-Priority Revision Roadmap

1. Complete review-priority baselines:
   - full priority
   - uncertainty-only
   - shrinkage-only
   - self-IoU-only
   - without morphology_delta

2. Add threshold sensitivity:
   - high-uncertainty threshold
   - review fraction threshold
   - self-consistency threshold
   - shrinkage threshold

3. Build a citation-backed Related Work table:
   - tunnel/crack segmentation
   - SegFormer-style segmentation
   - TTA and fusion
   - uncertainty/disagreement for segmentation
   - morphology/skeleton metrics
   - human-in-the-loop review

4. Generate visual artifacts for the case pack:
   - C1 fixed-fusion harm
   - C3 uncertainty/error overlap
   - C4 review queue
   - C6 high disagreement
   - C7 morphology degradation

5. Add a validity section:
   - internal validity: GT dependence, thresholds, baseline comparisons
   - external validity: one dataset, tunnel environment coverage
   - construct validity: review priority is not structural safety
   - reproducibility: scripts, configs, checkpoints, JSON evidence

## Score Summary

| Dimension | Score | Descriptor | Notes |
|---|---:|---|---|
| Originality | 72 | Adequate-to-Strong | Combination is useful; novelty depends on review-priority and harm-detection evidence. |
| Methodological Rigor | 62 | Adequate | Core evidence exists; ablations and sensitivity are pending. |
| Evidence Sufficiency | 64 | Adequate | Internal evidence is good, but comparative and external evidence are incomplete. |
| Argument Coherence | 78 | Strong | Claim boundaries are unusually clear. |
| Writing Quality | 72 | Adequate | Outline is clear, but not yet manuscript-level prose. |
| Literature Integration | 45 | Weak | Topic list exists; checked citations are missing. |
| Significance & Impact | 76 | Strong | Practical review workflow is meaningful for inspection QA. |

Weighted overall: approximately 68/100.

Editorial decision mapping: Minor Revision by score alone, but Major Revision by issue severity because ablations and literature positioning are required before submission.

## Paper-Safe Claims After Review

Safe now:

- The trained SegFormer B1 is the current mask source, not the invention.
- Fixed fused output can reduce foreground quality on labelled samples.
- Adaptive selected mask recovers fixed-fused loss in the current evaluation.
- Review priority can be computed without GT and evaluated afterward with GT.
- No-GT uploaded images must not display true mIoU, true error overlap, or GT calibration.

Not safe yet:

- Morphology_delta independently improves review-priority ranking.
- The review-priority score outperforms simpler uncertainty-only or shrinkage-only baselines.
- The method generalises to other tunnel datasets or inspection environments.
- Uncertainty is fully calibrated unless calibration bins are generated and reported.

