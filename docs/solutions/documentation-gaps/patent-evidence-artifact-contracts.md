---
title: Keep patent evidence artifact contracts honest
date: 2026-06-16
category: documentation-gaps
module: patent evidence and web demo
problem_type: documentation_gap
component: documentation
severity: medium
applies_when:
  - "Patent or demo evidence packs include paths to generated image artifacts"
  - "Review queue evidence is shown in both JSON and the Web demo"
  - "A completed plan promises fields or views that may drift from implementation"
symptoms:
  - "Evidence pack listed representative-case artifact paths that did not exist in the checkout"
  - "Review queue plan promised artifact stems and GT availability, but top-k items did not expose them"
  - "Web documentation promised morphology_delta, but the page only showed aggregate morphology rows"
root_cause: inadequate_documentation
resolution_type: documentation_update
tags: [patent-evidence, artifact-contract, web-demo, morphology-delta, review-queue]
---

# Keep Patent Evidence Artifact Contracts Honest

## Context

The project added a patent-ready evidence layer around SegFormer confidence-risk outputs. It generated `experiments/patent_evidence_test_pack.json`, added `review_queue_summary`, and documented the U7 evidence loop in the README, patent notes, and plan.

A later code/doc review found that the evidence story was slightly stronger than the implementation:

- The pack listed representative-case paths under `experiments/confidence_risk/...`, but those image artifacts were not present in the checkout.
- The completed plan said review queue entries include artifact stems and GT availability, but the JSON top-k entries only exposed image, priority, score, reasons, selection metadata, and GT-derived flags.
- The Web page promised `morphology_delta`, but the visible UI only showed selected-mask morphology rows.

This is a documentation-contract problem, not a model-quality problem. The fix was to make the data contract explicit and then update the UI and docs to match the actual artifact lifecycle.

## Guidance

When an evidence pack is meant for teachers, patent notes, or software-copyright materials, distinguish three different things:

- **Artifact path template**: where an image would be written if the representative case artifacts are generated.
- **Artifact availability**: whether that file exists in the current checkout.
- **Artifact evidence**: an actually generated image/report that can be opened and inspected.

Do not let a field named `artifacts` silently imply that files already exist. Keep backward compatibility if older UI code reads `artifacts`, but add clearer fields:

```python
def _artifact_exists(artifacts: dict[str, str]) -> dict[str, bool]:
    return {name: Path(path).exists() for name, path in artifacts.items()}


enriched["artifact_stem"] = Path(str(example.get("image"))).stem if example.get("image") else None
enriched["artifact_path_templates"] = artifacts
enriched["artifact_exists"] = exists
enriched["artifact_paths_verified"] = bool(artifacts) and all(exists.values())
enriched["artifacts"] = artifacts
```

For review queues, include the fields promised by the plan and useful for case browsing:

```python
return {
    "image": image,
    "artifact_stem": Path(str(image)).stem if image else None,
    "gt_available": any(
        evidence.get(key) is not None
        for key in [
            "selected_vs_fused_mIoU",
            "selected_vs_single_mIoU",
            "fused_vs_single_mIoU",
            "error_high_uncertainty_fraction",
            "high_uncertainty_error_fraction",
        ]
    ),
    ...
}
```

For Web display, make sure every documented no-GT signal has a visible surface. In this case the page already showed selected-mask morphology, but not the relationship among `single`, `fused`, and `selected`. Add a small `morphology_delta` panel with the three pairwise comparisons:

- `single -> fused`
- `fused -> selected`
- `single -> selected`

Also escape strings from JSON before inserting them with `innerHTML`, especially image names and review reasons:

```javascript
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
```

## Why This Matters

Patent and teaching materials are judged less forgivingly than internal debug output. A JSON field that points at a missing file looks like a broken result, even if the code only intended it as a template.

Being explicit about `artifact_path_templates`, `artifact_exists`, and `artifact_paths_verified` prevents overclaiming. It lets the project say:

> The evidence pack has deterministic representative cases and path templates. These paths become clickable evidence only after the corresponding artifacts are generated.

That boundary is important because the project already separates other evidence types:

- GT-required metrics: true mIoU, class IoU, error overlap, uncertainty calibration.
- No-GT signals: self-consistency, uncertainty, disagreement, morphology, review priority, selected-mask rationale.
- Path templates: reproducible file destinations, not proof that a file exists.

The same principle applies to Web views. If documentation says self-uploaded images show `morphology_delta`, the page should surface it directly instead of requiring the user to open raw JSON.

## When to Apply

- Apply this whenever generated evidence JSON is committed for a demo, patent note, paper figure, or teacher review.
- Apply it when plans or README text say a field is available in the Web UI.
- Apply it after code review or doc review finds that a completed plan promises more than the implementation exposes.
- Apply it before sending a repository link as progress evidence.

## Examples

Regenerate the compact evidence and patent pack after changing the contract:

```powershell
python enhancement_evidence.py experiments/adaptive_fusion_eval_test_full.json --output experiments/enhancement_evidence_test_summary.json --patent-pack-output experiments/patent_evidence_test_pack.json
```

Check the regenerated pack:

```powershell
@'
import json
from pathlib import Path
pack = json.loads(Path("experiments/patent_evidence_test_pack.json").read_text(encoding="utf-8"))
q = pack["enhancement_evidence"]["review_queue_summary"]["top_k"][0]
ex = pack["representative_examples"]["small_defect_guard"]
print(q["artifact_stem"], q["gt_available"])
print(pack["artifact_contract"])
print(ex["artifact_paths_verified"], ex["artifact_exists"]["selected_mask"])
'@ | python -
```

Expected meaning:

- `artifact_stem` is present for queue browsing.
- `gt_available` tells whether GT-derived evaluation flags are meaningful.
- `artifact_paths_verified: false` means the path is currently a template, not a clickable artifact.

Guard the contract with tests:

```python
assert result["artifact_contract"]["artifacts_are_path_templates"] is True
assert stable_example["artifact_path_templates"]["selected_mask"].endswith("_selected_mask.png")
assert stable_example["artifact_exists"]["selected_mask"] is False
assert result["top_k"][0]["artifact_stem"] == "needs_review"
assert result["top_k"][0]["gt_available"] is True
```

For Web demos, validate both the static page and the scripts:

```powershell
node -e "const fs=require('fs'); const html=fs.readFileSync('web_demo/index.html','utf8'); const scripts=[...html.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]); for (const s of scripts) new Function(s); console.log('scripts ok', scripts.length);"
```

## Related

- `docs/solutions/best-practices/enhancement-evidence-from-evaluation-json.md`
- `enhancement_evidence.py`
- `web_demo/index.html`
- `tests/test_enhancement_evidence.py`
- `tests/test_docs_artifact_contract.py`
- `experiments/patent_evidence_test_pack.json`
