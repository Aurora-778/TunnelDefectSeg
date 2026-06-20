# 隧道病害项目 PPT 视觉核对说明

Deck reviewed: `C:/Users/26822/Downloads/data/docs/presentations/tunnel-defect-project/index.html`

## Review Scope

当前演示稿是 HTML PPT，不是 `.pptx`。因此本次没有执行 PowerPoint OOXML 提取、PPTX 渲染和 notes pane 注入。讲稿基于 HTML 源内容、项目实验摘要、Web demo 资产路径和当前页面结构编写。

## Slide Coverage

| Slide | Visual Elements | Speaking Coverage |
|---:|---|---|
| 1 | selected overlay、selected mask、skeleton 三类结果图 | 讲清楚系统输出不只是 mask，而是检测报告。 |
| 2 | 三个问题卡片 | 覆盖形状画歪、无 GT 不可算 mIoU、只给 mask 不够。 |
| 3 | 数据规模卡片、6 类类别卡片 | 覆盖 1000 张、700/150/150、384x384、GT 定义。 |
| 4 | 输入到报告的流程图、训练/评估/演示三卡片 | 覆盖系统流水线和不同阶段能不能算 mIoU。 |
| 5 | SegFormer 分数卡、类别 IoU 表 | 覆盖 mIoU 84.33%、aAcc 98.62%、blocky 短板。 |
| 6 | decision_loop 伪终端、single/fused/hybrid 卡片 | 覆盖自适应选择和 fixed fusion 风险。 |
| 7 | 四个创新点卡片 | 覆盖专利可主张内容和不能夸大的边界。 |
| 8 | mIoU 对比表和提升条形图 | 覆盖 selected 相比 fixed fused 的提升，以及不声称总是超过 single。 |
| 9 | 小病害保护和 uncertainty 两组证据 | 覆盖 1,230,616 pixels、78.27% HU error precision。 |
| 10 | Web demo overlay、selected mask、skeleton 截图 | 覆盖拖拽检测、多视图、无 GT 时不报真实 mIoU。 |
| 11 | Civil / Track / Equipment 三领域卡片 | 覆盖 multidomain schema、demo adapter 和 spatial mapping 边界。 |
| 12 | 专利方向卡片 | 覆盖可写权利要求和不可主张项。 |
| 13 | 完成情况和下一步 | 覆盖当前成果、blocky 优化和专利案例整理。 |

## Visual Consistency Notes

- Web 和 PPT 的 skeleton 应尽量使用同一套 selected mask 生成逻辑，否则老师会问为什么骨架形状不同。讲稿中已把 skeleton 定义为“从最终选择的 mask 提取的形态证据”。
- 第 8 页的增强模块数据和第 5 页 SegFormer mIoU 属于两层实验，不要混着讲。第 5 页讲模型能力，第 8 页讲 fixed fusion 与 selected 的相对对比。
- 第 10 页自选图片没有 GT 时，不能显示真实 mIoU。讲稿已准备标准解释。
- 第 11 页是多领域扩展页，重点是 civil 主线已经完成，track / equipment 是 demo adapter，spatial mapping 已有 prototype 实现，但仍按 simulation/calibration 边界讲。

## Uncertain Or Limited Elements

- 未进行真实浏览器截图逐像素核对；如果最终展示前页面又改版，建议重新打开 `http://127.0.0.1:8000/` 检查第 10 页引用图和 Web 实际输出是否一致。
- 当前没有 `.pptx` 文件，所以没有生成 `<deck-stem>-with-notes.pptx`。本目录中的 Markdown 文件就是展示时使用的正式讲稿。
