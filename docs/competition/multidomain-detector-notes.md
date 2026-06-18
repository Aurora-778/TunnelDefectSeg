# Multidomain Detector Notes

这份说明对应计划里的 U4：先把轨道和设备结果做成可插拔适配层，再逐步替换成真实模型。

## What the adapter does

- 统一轨道和设备的结果结构。
- 允许 demo / simulation / future-model 三种来源并存。
- 让结果先进入同一份 `multidomain-result.v1` 合同，再交给报告导出和 Web 展示。

## Demo labels

- `track / fastener_missing`
- `equipment / bracket_loose`
- `equipment / foreign_object_intrusion`

## Current rule

- 当前仓库只提供 demo 适配器，不声称已经训练出完整轨道/设备模型。
- 没有真实样本时，结果必须标注 `simulation-only` 或 `planned`。
- 轨道和设备结果可以先用 bbox / point / line 结构表达，不强求第一版做到像素级 mask。

## Why this matters

比赛和软著材料里，最重要的是证明系统能接纳多领域病害，而不是把所有领域都先训练到位。这个适配器层让土建 SegFormer 输出和后续轨道/设备结果使用同一个报告管道，便于扩展、展示和写材料。
