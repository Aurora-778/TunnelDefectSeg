# Association Benchmark Report

## 数据与边界

本 benchmark 使用提交到 `tests/fixtures/association_benchmark/` 的困难合成案例。它用于检验规则的候选选择、拒识与人工复核边界，不代表真实线路部署准确率。
`weighted_no_id` 直接调用生产 `AssociationAgent` 的 no-id 评分、阈值与复核策略；`with_id_upper_bound` 仅为标签可见时的上界 / sanity check。

## 场景

- 案例数：18
- Ground Truth action：match 8，reject 3，manual_review 7
- 覆盖：里程/方位漂移、相似候选、面积与空间冲突、新病害、字段缺失、风险变化及未来候选过滤。

## 统一指标

| strategy | top1_accuracy | accepted_accuracy | rejection_rate | manual_review_rate | conflict_rate | false_match | false_reject | mean_margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| nearest_mileage | 0.9333 | 1.0000 | 0.2222 | 0.2778 | 0.2778 | 0 | 1 | 0.5437 |
| area_only | 0.9333 | 0.7778 | 0.0556 | 0.4444 | 0.4444 | 2 | 0 | 0.3700 |
| spatial_only | 1.0000 | 1.0000 | 0.1667 | 0.3333 | 0.3333 | 0 | 0 | 0.4939 |
| weighted_no_id | 0.9333 | 1.0000 | 0.1111 | 0.4444 | 0.4444 | 0 | 0 | 0.3194 |
| with_id_upper_bound | 1.0000 | 1.0000 | 0.1111 | 0.1111 | 0.1111 | 0 | 0 | 0.3964 |

## 真实结论

- weighted_no_id 相对 nearest_mileage：未优于 nearest_mileage（以本 fixture 的 top-1 accuracy 为准）。
- weighted_no_id 相对 spatial_only：未优于 spatial_only（0.9333 vs 1.0000）。
- 自动接受、拒识和人工复核均由同一逐案例结果表统计；错误接受和错误拒识不会被隐藏。
- 当前 benchmark 不能证明真实机器人连续巡检中的长期泛化能力，也不能替代带跨巡检 GT 的现场验证。

## 重复命中边界

C11/C12 是两个独立逐帧 query 对同一历史对象的重复命中案例；本 benchmark 不执行 one-to-one 分配，也不声称解决 query 间唯一匹配问题。

## 自动接受案例

逐案例结果中 `predicted_action=match` 的记录可见于 `association_benchmark_cases.csv`。

## 拒绝案例

逐案例结果中 `predicted_action=reject` 的记录可见于 `association_benchmark_cases.csv`。

## Manual Review 案例

逐案例结果中 `predicted_action=manual_review` 的记录保留为人工复核边界，而不是强行自动关联。

## False Match 与 False Reject

两个错误计数来自统一逐案例表；报告不删除失败案例，也不把 with-id 结果当作 no-id 结果。

## weighted_no_id 的优势与局限

该策略同时使用生产规则中的空间、面积、时间与风险信号，因此可在单一信号不足时保留复核选项。但若简单 baseline 指标相同或更高，报告会如实保留该局限，不得据此声称普遍优越。

## 策略说明

- nearest_mileage：仅里程最近的简单 baseline，不推荐作为生产策略。
- area_only：仅面积相似度 baseline。
- spatial_only：仅里程和方位的空间 baseline。
- weighted_no_id：生产 no-id Association。
- with_id_upper_bound：标签上界，不是可部署 baseline。
