# Civil Defect Evaluation

这份说明用于把当前 6 类土建分割结果翻译成比赛更容易理解的口径。它的作用是“展示时更像工程语言”，不是重新定义数据集标签。

## Current Label Mapping

| Dataset label | Competition wording | Usage note |
|---|---|---|
| `background` | 背景 | 非病害区域，不单独作为结果展示重点。 |
| `simple` | 简单类病害 | 当前数据集原始标签，展示时可写成“simple 类”。 |
| `blocky` | 块状类病害 | 当前最弱类，适合作为改进重点。 |
| `pipeline` | 管线类 / 线状类病害 | 保留英文标签时可直接写 `pipeline`。 |
| `vertical` | 竖向类病害 | 适合在报告中按方向解释。 |
| `horizontal` | 横向类病害 | 适合在报告中按方向解释。 |

## Reporting Rule

- 论文、专利和比赛材料可以把当前 6 类看成“土建结构病害的内部标签空间”。
- 对老师或评委展示时，优先使用“civil structure / 土建结构”作为大类，再在小类里展示 `simple / blocky / pipeline / vertical / horizontal`。
- 没有官方比赛样本时，不要把这 6 类直接说成最终比赛官方 taxonomy。

## Why This Matters

比赛更关心的是系统是否能把土建病害识别、可信复核和工程化报告串起来，而不只是模型内部类别名。这个映射让现有 SegFormer 结果能直接进入统一报告 schema，同时为后续轨道和设备结果预留相同的展示位置。
