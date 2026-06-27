# Association Progressive Evaluation Report

说明：本报告使用仿真 disease_id 作为评估标签；匹配阶段禁用 disease_id 得分、hard 判定和解释文本，只允许使用历史 memory、空间、面积、时间和风险规则。

## Round 1: query I002

- history inspections: I001
- manual review rate: 1.0
- unmatched rate: 0.0
- conflict count: 0

### Baseline / Ablation

- area_only: top-1 accuracy 0.5
- nearest_mileage: top-1 accuracy 1.0
- same_disease_id: top-1 accuracy 1.0
- weighted_score_no_id: top-1 accuracy 1.0

### Failure / Uncertain Cases

- I002_000040: label=D001, weighted=D001, manual_review=true, score=0.7833
- I002_000041: label=D002, weighted=D002, manual_review=true, score=0.7833
- I002_000018: label=D003, weighted=D003, manual_review=true, score=0.7833
- I002_000180: label=D004, weighted=D004, manual_review=true, score=0.7833
- I002_000013: label=D005, weighted=D005, manual_review=true, score=0.7833

## Round 2: query I003

- history inspections: I001, I002
- manual review rate: 1.0
- unmatched rate: 0.0
- conflict count: 0

### Baseline / Ablation

- area_only: top-1 accuracy 0.5
- nearest_mileage: top-1 accuracy 1.0
- same_disease_id: top-1 accuracy 1.0
- weighted_score_no_id: top-1 accuracy 1.0

### Failure / Uncertain Cases

- I003_000040: label=D001, weighted=D001, manual_review=true, score=0.7833
- I003_000041: label=D002, weighted=D002, manual_review=true, score=0.7833
- I003_000018: label=D003, weighted=D003, manual_review=true, score=0.7833
- I003_000180: label=D004, weighted=D004, manual_review=true, score=0.7833
- I003_000013: label=D005, weighted=D005, manual_review=true, score=0.7833
