# Association Evaluation Report

## Evaluation Setting

本评估使用 **progressive evaluation**，按 inspection 时间顺序增量更新 memory，避免 future memory leakage。

- 当前 inspection 只能与过去已经看过的 memory 匹配；
- 不能提前看到未来 inspection；
- 不使用 full pipeline batch rebuild 的全量 `disease_memory_bank.csv` 作为初始 memory；
- 每个 round 的 memory 由历史 inspection 增量构建。

## No-ID Evaluation

no-id 是 **primary evaluation**，disease_id 不参与真实 matching。

- total_records: 20
- label_evaluable_count: 20
- top1_accuracy: 1.0
- rejection_rate: 0.0
- matched_count: 20
- unmatched_count: 0
- uncertain_count: 0
- manual_review_count: 20
- mean_association_score: 0.9155

## With-ID Upper Bound

with-id 仅作为 **upper-bound / sanity check**，不是正式结论。disease_id 在此模式参与评分，
用于验证 no-id 策略与理想上界的差距。

- total_records: 20
- label_evaluable_count: 20
- top1_accuracy: 1.0
- rejection_rate: 0.0
- matched_count: 20
- unmatched_count: 0
- uncertain_count: 0
- manual_review_count: 0
- mean_association_score: 0.9281

## Comparison

- matched_rate_difference (no-id - with-id): 0.0
- manual_review_difference (no-id - with-id): 1.0
- score_difference (no-id - with-id): -0.0126

## Baseline / Ablation

- no-id baseline: disabled disease_id scoring; disease_id is also disabled during ranking, match typing, confidence, and conflict checks.
- with-id ablation: disease_id is enabled only as an upper-bound / sanity check.
- 主结论以 no-id baseline 为准，with-id ablation 不覆盖主 pipeline 输出。

## Leakage Control

明确说明：本 progressive evaluation 没有使用 full pipeline batch memory 作为初始 memory。
每个 round 的 memory 由历史 inspection 增量构建，当前 inspection 只能看到过去。

## Limitations

当前 ground truth / disease_id 仍来自仿真数据，不代表真实连续巡检 GT。
with-id upper-bound 依赖 disease_id 标签，真实场景中该标签不可用。

## Progressive Rounds

### Round 1: query I002

- history inspections: I001
- query frame count: 10
- no-id matched: 10
- with-id matched: 10

### Round 2: query I003

- history inspections: I001, I002
- query frame count: 10
- no-id matched: 10
- with-id matched: 10

## No-ID Error Cases (first 20)

- none

## With-ID Error Cases (first 20)

- none
