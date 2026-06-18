# Inspection Report Template

本模板用于把单图巡检结果整理成可以导出、展示和归档的结构化报告。它不替代原始 `*_report.json`，而是给老师、评委和后续比赛材料准备一个更稳定的交付层。

## Recommended contract

- `schema_version`: 报告结构版本，便于后续升级字段而不破坏兼容性。
- `job_id`: 本次检测任务 ID。
- `source_image`: 原图名称和 `stem`，不写本机绝对路径。
- `model`: `mask_source`、`tta_specs` 等模型来源信息。
- `assessment`: `risk`、`review_priority`、`adaptive_selection` 的可读汇总。
- `evidence`: `self_consistency`、`uncertainty_summary`、`disagreement_summary`、`morphology`、`morphology_delta`。
- `views`: 原图、mask、overlay、uncertainty、disagreement、skeleton 等可视化链接。
- `multidomain_results`: 统一的 civil / track / equipment 结果列表，便于比赛展示和后续扩展。
- `artifacts`: 导出文件名清单，只保留相对文件名。
- `summary_cards`: 适合网页和答辩展示的高层摘要卡片。

## File naming

- 原始巡检报告：`<stem>_report.json`
- 结构化巡检报告：`<stem>_inspection_report.json`

## Display guidance

- Web 端优先展示 `assessment` 和 `summary_cards`，让老师先看结论，再看细节。
- 有 GT 的样本可以附带 `evaluation`，没有 GT 的自选图片不要写真实 mIoU。
- `uncertainty`、`disagreement`、`morphology_delta` 要单独保留，便于解释增强模块的价值。
- 报告里不要放本机盘符、用户目录或本地环境私有路径。

## Minimal example

```json
{
  "schema_version": "inspection-report.v1",
  "job_id": "20260618-acde1234",
  "source_image": {"name": "t1_1.jpg", "stem": "t1_1"},
  "assessment": {
    "risk": {"risk_level": "low", "score": 1.75},
    "review_priority": {"priority": "low", "score": 0.5},
    "adaptive_selection": {"mode": "fused"}
  }
}
```

## Why this matters

比赛展示不仅要“能跑”，还要“能讲清楚”。这个模板把模型输出、证据链、导出文件和页面展示统一到同一份合同里，后续做比赛材料、软著材料和技术说明时都能直接复用。
