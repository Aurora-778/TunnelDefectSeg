# Association Rule Design

本文档解释当前机器人隧道巡检病害监测项目中的病害关联规则。它用于答辩、代码审查和后续迭代时统一口径，避免把仿真标签误说成真实匹配能力。

## 1. 为什么需要 Association

机器人连续巡检时，同一处病害可能在多次巡检、多个图像帧中重复出现。系统需要把当前帧中的病害记录和历史 Disease Memory Bank 中的病害对象关联起来，才能生成：

- 同一病害的跨巡检记录；
- 规则面积变化提示；
- 重点复检清单；
- 工程化描述和最终报告。

当前 Association 是一个规则基线，用来验证数据链路和工程表达，不是学习式视觉 re-identification 模型。

## 2. 为什么主流程不使用 disease_id

`disease_id` 来自仿真元数据或评估标签。它可以帮助我们在评估阶段判断“匹配结果是否正确”，但不能参与主流程的匹配评分。

更通俗地说：`disease_id` 像考试答案，不能拿答案做题，只能做完题以后用来对答案。

因此当前主 pipeline 固定使用：

- `use_disease_id_score=false`
- `association_mode=no_id`

在这个模式下，`disease_id` 只作为 `label_disease_id` 或评估标签保存，不参与 `association_score`、`match_type` 或 `rule_basis` 的主流程判断。

## 3. no-id matching 的证据来源

主流程的关联分数来自非 ID 规则证据：

- `spatial_distance_score`：里程、环号、方位等工程空间信息是否接近；
- `area_similarity_score`：mask 面积是否相近；
- `temporal_continuity_score`：巡检顺序是否符合时间递进；
- `risk_similarity_score`：风险等级和关注等级是否一致或接近；
- `score_margin`：第一候选和后续候选之间的分数差距；
- `candidate_count` / `top_candidate_ids`：候选竞争情况。

这些证据只能说明“当前规则下更像同一处病害”，不能直接等同于现场确认结果。

## 4. with-id upper-bound 的含义

progressive evaluation 会额外生成 with-id 结果：

- `use_disease_id_score=true`
- `association_mode=with_id_upper_bound`

with-id 只作为 upper-bound / sanity check，用于观察“如果评估标签可见，上限会是多少”。它不进入主 DAG，不代表真实使用时可以读取 `disease_id`。

答辩时可以这样表述：主结果看 no-id，with-id 只是对照上界。

## 5. 人工复核机制

当候选分数接近、规则证据冲突或置信度不足时，系统会输出：

- `needs_manual_review`
- `conflict_reason`
- `confidence_level`
- `match_type`

这些字段用于把不确定样本交给人工复核，而不是强行给出确定结论。当前系统强调“辅助复检”，不把规则关联结果包装成真实工业闭环中的最终判定。

## 6. 当前限制

- KICT 是静态公开 image/mask 数据集，不是同一线路的真实连续巡检序列。
- 时间、里程、环号、方位、`disease_id` 和跨巡检关系来自仿真元数据。
- 当前关联是规则基线，不包含 DINOv2、SAM、Grounding DINO、视觉 re-ID 或真实机器人位姿约束。
- 规则面积变化提示不是长期病害预测，也不是结构安全结论。
- Web Dashboard 是本地演示界面，不代表生产级巡检平台。

## 7. 推荐答辩表述

本项目把 KICT 静态裂缝 mask 的几何信息接入机器人巡检仿真元数据，构建了病害对象记忆、no-id 规则关联、规则面积变化提示和复检清单生成流程。主流程不使用 `disease_id` 做匹配评分，`disease_id` 只用于评估对照；with-id 结果只作为 progressive evaluation 的上界检查。当前系统是工程原型，重点证明数据链路、规则证据和复检表达，不声称已经具备真实工业长期预测能力。
