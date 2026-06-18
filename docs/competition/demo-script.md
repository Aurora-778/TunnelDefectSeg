# Demo Script

这份脚本用于比赛答辩和老师现场演示。目标是 5 到 8 分钟内讲清楚输入、检测、解释、复核和多领域扩展。

## Opening

先说一句总目标：

“这个项目不是只训练一个分割模型，而是做一个能自动找隧道病害、能解释结果、能提示复核风险的检测系统。”

## Demo Flow

1. 打开 Web 页面，说明可以拖拽图片。
2. 先展示数据集内样本。
3. 指出原图、GT 标注、single mask、selected mask、overlay、skeleton。
4. 说明有 GT 的样本可以算真实 mIoU，因为 GT 就是人工标注 mask。
5. 再拖入一张自选图片。
6. 说明自选图片没有 GT，所以真实 mIoU 显示 N/A，不能乱报准确率。
7. 切换 uncertainty、disagreement、skeleton，说明这是模型证据，不是新的 GT。
8. 打开报告导出，说明系统会把结果整理成结构化 report。
9. 展示多领域摘要，说明 civil / track / equipment 已经统一到同一份合同里。

## Key Talking Points

- backbone 负责“画得准”
- adaptive selection 负责“别把小病害融合没了”
- review priority 负责“先看哪里”
- morphology 负责“把 mask 翻译成工程证据”
- multidomain schema 负责“让比赛展示不只停留在单一隧道缺陷”

## What To Say On The Slides

### Problem

“问题不只是能不能画出来，而是画得能不能信。”

### Backbone

“SegFormer 是底座，用来提升基础 mask 质量，不是我的核心创新点。”

### Enhancement

“固定融合会抹掉小病害，所以我让系统自动比较候选 mask，再选择更可靠的结果。”

### Innovation

“我的创新不是发明 SegFormer，而是把检测后的判断流程、解释流程和复核流程做完整。”

### Metrics

“selected 不是永远超过 single，但它能明显修复 fixed fusion 带来的损失。”

### Multidomain

“civil 是当前已经完成的主线，track 和 equipment 先用 demo adapter 接进来，空间定位先作为 planned / prototype 处理。”

### Conclusion

“最后不是只输出一张 mask，而是输出一份能让人快速判断、快速复核的工程证据报告。”

## Likely Questions

1. 为什么换 SegFormer，而不是继续用原模型？
2. 你的创新点到底在哪里？
3. 为什么 selected 不总是比 single 高？
4. 没有 GT 的图片为什么不能算 mIoU？
5. blocky 为什么还是短板？
6. 多领域扩展是不是已经训练好了？
7. spatial mapping 现在能做到什么程度？

## Short Answers

- SegFormer 是更强的底座，创新在后处理和复核链。
- selected 的价值是修复 fixed fusion 损失，不是绝对压过 single。
- 自选图片没有 GT，不能把模型自测当真实精度。
- track / equipment 目前是 demo adapter，后续可以换真实模型。
- spatial mapping 目前适合按 planned / prototype 讲，不夸大。

