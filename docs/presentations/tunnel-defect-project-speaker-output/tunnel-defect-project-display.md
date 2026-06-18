# 隧道病害语义分割与可信增强展示讲稿

Deck path: `C:/Users/26822/Downloads/data/docs/presentations/tunnel-defect-project/index.html`

输出语言：中文。专业名词保留英文，例如 `SegFormer`、`mask`、`GT`、`mIoU`、`uncertainty`、`IoU`。

## Deck Comprehension Brief

**Thesis**: 这个项目不是只做一个分割模型，而是做一个能自动找隧道病害、能解释结果、能提示复核风险的检测展示系统。

**Structure**: 先讲问题，再讲数据和系统流程，然后讲 SegFormer 提升基础 mask 质量，接着讲自适应增强模块如何选择更可靠的 mask，最后用实验结果、Web demo 和专利方向收束。

**Methods**: 6 类语义分割、SegFormer B1、mIoU 评估、自适应 mask selection、fixed fused 对照、uncertainty 复核提示、形态学骨架和连通域分析、Web drag-and-drop demo。

**Key parameters**: 数据集 1000 张，训练/验证/测试为 700/150/150，输入尺寸 384 x 384，SegFormer B1 训练到 160000 iter，最终 mIoU 84.33%，mAcc 91.17%，aAcc 98.62%。

**Visual evidence**: PPT 中包含 overlay、selected mask、skeleton、指标表、流程图、Web demo 截图和实验结果表。讲的时候重点解释“模型画出的区域”和“系统给出的复核依据”。

**Coverage notes**: 当前是 HTML PPT，不是 `.pptx` 文件，所以没有向 PowerPoint notes pane 注入备注。本文件作为完整展示讲稿使用。

## Narrative Arc

**Opening**: 现实问题不是“模型能不能画一个 mask”，而是“这个 mask 准不准、能不能被人相信”。

**Middle**: SegFormer 先提升基础分割质量，增强模块再解决 fixed fusion 可能抹掉小病害、上传图片没有 GT、结果缺少解释的问题。

**Close**: 项目已经形成“模型检测 + 可信选择 + 复核证据 + Web 展示”的完整链路，后续可以继续优化 blocky 类和专利材料。

## Opening Script

各位老师好，我这个项目做的是隧道病害图像的自动检测。简单说，就是输入一张隧道图片，系统自动把疑似病害区域画出来，并且进一步告诉用户：它画的是哪一块、形状大概是什么、这个结果靠不靠谱、是否需要人工复核。

我想强调的是，这个项目不是只训练一个模型。单纯训练模型只能回答“哪里可能有病害”。但是实际展示和应用时，还会遇到几个问题：模型可能把细裂缝画成一块不真实的形状；老师现场拖入一张新图片时没有人工标注，就不能乱报 mIoU；只给一张 mask 也很难说明为什么这个结果值得相信。所以我把工作拆成两部分：第一部分用 SegFormer 提高基础分割质量，第二部分在模型结果后面加一层可信增强模块，让输出结果更容易判断和复核。

## Slide-by-Slide Display Notes

### [Slide 1 - 给隧道图片自动找病害]

这个系统的目标是把一张普通隧道图片转成一份可以检查的病害检测报告。

左边标题说的是“给隧道图片自动找病害”，右边几张图对应系统输出的不同视角：overlay 是把病害区域叠加回原图，mask 是模型预测出的病害区域，skeleton 是把病害区域进一步细化成骨架，方便看方向和形状。

我希望老师先记住一个核心思路：模型负责把病害区域画出来，后面的增强模块负责解释这个结果，并判断哪些图片更应该优先人工复核。也就是说，最后给用户看的不只是一个彩色块，而是一组可复核的证据。

Transition: 接下来先看为什么这个问题不能只靠“画出 mask”来解决。

### [Slide 2 - 问题不只是能不能画出来]

隧道病害检测的难点不只是模型能不能画出一块区域，而是这块区域画得能不能信。

第一，病害形状本身比较复杂。比如细裂缝、折角、块状剥落，模型有时会把它们画成比较规整的一团，看起来和真实病害不一致。第二，现场演示时如果拖入一张新图片，这张图片通常没有人工标注，也就是没有 GT，这时不能计算真实 mIoU。第三，只给 mask 不够，工程上还需要知道面积、方向、骨架、连通域，以及是否建议人工复核。

所以这个项目的思路是两层：先让模型画得更准，再让结果更容易被人检查和理解。

Transition: 为了让训练和评估可信，第一步先把数据统一好。

### [Slide 3 - 数据集和类别标准]

数据统一是后面所有实验的基础。

这个项目目前使用 1000 张隧道图片和对应的人工标注 mask。数据被整理成 6 类：background、simple、blocky、pipeline、vertical、horizontal。训练、验证、测试的划分是 700、150、150，输入尺寸统一为 384 x 384。

这里的 GT，就是数据集里人工标好的 mask。训练时模型会用图片和 GT mask 学习每个像素属于哪一类；评估时也要拿预测 mask 和 GT mask 对比，才能算出 mIoU、IoU、Acc 这些指标。没有 GT 的自选图片，只能展示预测结果和自一致性证据，不能算真实 mIoU。

Transition: 数据准备好之后，系统就可以按一条完整流程运行。

### [Slide 4 - 系统流程]

整套系统可以理解成一条从图片到报告的流水线。

第一步输入隧道图片。第二步由分割模型输出病害 mask。第三步增强模块比较不同候选结果，判断是使用 single mask、fused mask，还是保守选择更可靠的结果。第四步从 mask 中提取证据，比如骨架长度、面积、方向、连通域和风险提示。第五步在有人工标注的样本上检查 uncertainty 和真实错误是否对应，在没有标注的上传图片上只给出复核优先级，不乱报真实精度。最后把这些内容放到 Web 页面里，让用户可以切换视图查看。

训练时，有 GT，可以学习和评估。评估时，有 GT，可以计算 mIoU。演示时，如果用户自己拖图片进来，没有 GT，就不展示真实 mIoU，而是展示 mask、overlay、skeleton、uncertainty 或自一致性信息。

Transition: 系统的第一层能力来自更强的基础分割模型。

### [Slide 5 - SegFormer 提升基础分割质量]

第一步改进是把基础分割模型换成 SegFormer B1，让病害区域画得更像真实标注。

最终训练到 160000 iter，整体 mIoU 达到 84.33%，mAcc 是 91.17%，aAcc 是 98.62%。从类别结果看，pipeline、vertical、horizontal 的 IoU 比较高，分别达到 94.16、95.85 和 83.17。simple 类也达到 80.23。blocky 类目前是短板，IoU 是 53.58。

这说明 SegFormer 已经能作为比较强的 mask 来源，但它不是我最核心的创新点。SegFormer 是已有模型，我这里把它作为底座，解决“先画得准”的问题。真正的差异化在后面的可信增强和解释流程。

Transition: 模型画完之后，系统还会判断哪一个输出更适合给人看。

### [Slide 6 - 自适应增强模块]

增强模块的作用可以理解成模型结果后面的检查员。

模型可能产生 single mask，也可能通过翻转等方式得到 fused mask。传统做法可能直接固定融合，但在这个数据里，固定融合有时会把小病害抹掉。所以增强模块会比较候选 mask：哪一个更稳定，哪一个保留了小病害，哪一个更适合作为最终输出。现在又增加了 review priority，也就是人工复核优先级，用来回答“这张图是不是应该先让人看”。

系统现在主要支持三种选择逻辑：single 表示保留单次预测结果，fused 表示采用融合结果，hybrid 或 selected 表示根据规则自适应选择。选完之后，还会把 mask 转成可解释证据，比如骨架、面积、方向、连通域、复核提示和 review priority。

Transition: 这也是我认为项目最适合写成创新点的地方。

### [Slide 7 - 创新点]

这个项目的创新点不是“发明一个新的大模型”，而是在病害分割结果后面加了一层会判断、会解释的机制。

第一，它能发现 fixed fusion 反而变差的情况。比如小裂缝或小块病害被融合过程抹掉，系统会倾向于保留更可靠的 single mask。第二，它不是固定使用某一种结果，而是自动选择当前样本更合适的 mask。第三，它把 mask 继续转成骨架长度、方向、连通域、面积和复核理由，让检测结果从“一个色块”变成“可检查的证据”。第四，它不会在无 GT 的上传图片上乱报真实 mIoU，这一点对展示和实际使用很重要。

如果写专利，方向可以概括为：一种面向隧道病害分割结果的置信感知自适应输出选择与复核证据生成方法。

Transition: 下面用实验结果说明这个增强模块具体带来了什么。

### [Slide 8 - 实验结果]

实验结果的核心结论是：fixed fusion 会明显损失一部分小病害，而 selected 能把这部分损失找回来。

在 test split 的 150 张样本上，single mIoU 是 0.3305，fixed fused mIoU 是 0.3165，selected mIoU 是 0.3306。selected 相比 fixed fused 提升 0.0141。对于全部 1000 张有标注样本，single mIoU 是 0.3725，fixed fused 是 0.3287，selected 是 0.3680。selected 相比 fixed fused 提升 0.0393。

这里要讲得诚实：selected 不是在所有情况下都超过 single。在全部样本上，它比 single 低 0.0044。所以不能说“增强模块一定超过单次预测”。更准确的说法是：增强模块能识别并减少 fixed fusion 抹掉小病害的问题，让系统不要盲目融合。

Transition: 除了 mIoU，增强模块还提供了更适合人工复核的证据。

### [Slide 9 - 具体价值]

增强模块带来的价值主要有三个：保住小病害，提示复核区域，并给出复核优先级。

从全部 1000 张有标注样本看，系统保护了 1,230,616 个相对 fixed fusion 被保留下来的前景像素。successful guard 是 614/1000，selected recovered 是 699/1000。这说明固定融合确实经常会削弱前景，而自适应选择能在很多样本上把这部分病害区域保住。

uncertainty 这部分可以理解为系统提示“这里可能更容易错”。在全部样本里，高不确定性区域中有 78.27% 是预测错误像素，error coverage 是 44.54%。这说明 uncertainty 不是随便热，而是对人工复核有参考价值。

Transition: 这些结果最后都被做进了 Web demo。

### [Slide 10 - Web 展示]

Web demo 的作用是把训练结果和增强模块变成一个能现场演示的工具。

用户可以直接把图片拖进去，系统生成多视图报告，包括原图、single mask、fused mask、selected mask、overlay、uncertainty、disagreement 和 skeleton。对于数据集样本，如果有 GT，就可以展示 mIoU 等真实评估指标；对于用户自己上传的图片，因为没有 GT，就不展示真实 mIoU，而是展示可复核的结果和解释。

这里的 skeleton 很重要。它不是新的分割结果，而是从最终选择的 mask 里提取出来的形态证据。它能帮助老师快速看病害方向、细长程度和连通结构。

Transition: 有了模型、增强模块和 Web 展示，就可以进一步整理专利表达。

### [Slide 11 - 多领域扩展]

这一页要把比赛方向说清楚：主线已经是 civil 病害可信增强，扩展线则是把同一套结果合同继续接到 track 和 equipment 上。

左边先说 civil 已经完成，SegFormer B1、adaptive selection、review priority 和 report export 都已经接上。中间和右边分别说 track / equipment 先用 demo adapter 接入示例结果，统一到 multidomain schema 里，后续可以替换成真实训练模型。

这一页还要明确 spatial mapping 的边界：当前只能按 planned / prototype 讲，不把仿真定位说成外业级精确定位。

Transition: 接下来再回到专利表达，把“模型之后的判断流程”收束一下。

### [Slide 12 - 专利方向]

专利表达的重点应该放在“检测后的判断流程”，而不是把 SegFormer 当成创新。

可以主张的点包括：第一，基于候选 mask 的差异和置信信息，自动选择更可靠的输出；第二，识别 fixed fusion 抹掉小病害的情况，并保护前景证据；第三，从最终 mask 生成骨架、面积、方向、连通域和风险提示；第四，在无 GT 场景下不输出伪精度，而是输出自一致性和复核依据。

同时也要避免过度主张。SegFormer、TTA、uncertainty、skeletonization 都是已有技术，不能说是我单独发明的。更合理的创新是把这些能力组合成一个面向隧道病害检测的自适应选择和复核证据生成流程。

Transition: 最后一页总结目前完成情况和下一步工作。

### [Slide 13 - 总结和下一步]

目前项目已经形成一个能训练、能评估、能展示、能解释的完整系统。

已经完成的部分包括：数据集整理成 6 类标准格式；SegFormer B1 训练到 mIoU 84.33%；实现自适应 mask selection、基于 SegFormer 概率 TTA 的 uncertainty / disagreement 复核提示和形态学证据；做出了支持拖拽图片实时检测的 Web 展示。现在又补充了 U7 证据闭环：patent evidence pack、morphology delta、review queue 和软著说明材料；同时把 civil / track / equipment 的多领域结果合同接上了 demo adapter，让结果更适合给老师检查、整理专利交底和准备软著。

下一步可以从三方面继续推进。第一，优化 blocky 类，因为它目前 IoU 最低。第二，继续积累跨场景典型案例，让 review queue 里的高优先级样本覆盖更多真实巡检情况。第三，继续探索 temperature scaling 或 conformal prediction 这类更强的校准方法，把当前的复核优先级进一步做成更严谨的可信输出。

最后一句话总结这个项目：它不是只告诉用户“这里可能有病害”，而是进一步告诉用户“为什么这么判断，以及哪里需要再看一眼”。

## Demo Operation Script

1. 双击 `C:/Users/26822/Downloads/data/run_web_app.bat` 启动 Web 应用。
2. 打开 `http://127.0.0.1:8000/`。
3. 先选数据集内样本，展示原图、GT 标注、single mask、selected mask、overlay、skeleton。
4. 讲清楚：有 GT 的样本可以算 mIoU，因为 GT 就是人工标注 mask。
5. 再拖入一张自选图片，展示实时推理。
6. 讲清楚：自选图片没有 GT，所以真实 mIoU 显示为 N/A；页面只展示模型输出、自一致性、形态证据和复核提示。
7. 如果老师问骨架图，说明 skeleton 是从 selected mask 提取的形状摘要，用于看方向和结构，不是新的 GT。

## Teacher Q&A

### 1. 为什么换 SegFormer，而不是继续用原来的模型？

原来的模型在细小病害和复杂形状上容易把区域画得不准。SegFormer 使用 Transformer 结构，对全局上下文建模更强，所以更适合作为新的分割底座。当前 SegFormer B1 最终 mIoU 达到 84.33%，说明基础 mask 质量已经明显更适合后续展示和分析。

### 2. SegFormer 是已有模型，那你的创新在哪里？

创新不在“我发明了 SegFormer”。SegFormer 是底座。我的创新主要在模型输出之后：系统会用同一个 SegFormer checkpoint 生成 single 和 probability TTA fused 结果，比较候选 mask 的稳定性，识别 fixed fusion 抹掉小病害的情况，自适应选择更可靠的输出，并生成 uncertainty、disagreement、骨架、面积、方向、连通域、风险提示等可复核证据。

### 3. mIoU 84.33 是怎么来的？

这是 SegFormer B1 在有 GT 的评估集上计算出来的结果。mIoU 是各类别 IoU 的平均值，IoU 是预测区域和人工标注区域的交并比。训练到 160000 iter 后，日志记录的 mIoU 是 84.33%，mAcc 是 91.17%，aAcc 是 98.62%。

### 4. 为什么 Web 里有些 mIoU 看起来低？

Web 里可能展示的是增强模块对 single、fixed fused、selected 的后处理对比，数值范围和 SegFormer 最终 6 类评估不是同一层含义。SegFormer 的 84.33% 是基础模型在标准评估集上的结果；增强模块的 0.3305、0.3165、0.3680 主要用于比较 fixed fusion 和 selected 的相对变化，不能混在一起解释。

### 5. GT 是不是数据集里的 mask？

是。GT 就是 Ground Truth，也就是人工标注的标准 mask。训练时模型用图片和 GT mask 学习；评估时预测 mask 和 GT mask 对比，才能算 mIoU。

### 6. 训练有没有用到 mask？

用到了。语义分割训练必须使用图片和对应的 mask。图片是输入，mask 是监督信号。模型学习的是每个像素应该属于 background、simple、blocky、pipeline、vertical、horizontal 中的哪一类。

### 7. 自选图片能不能只用模型推理结果算 mIoU？

不能算真实 mIoU。mIoU 的定义需要预测结果和 GT 对比。自选图片只有模型预测，没有人工标注，所以没有真实标准答案。可以计算自一致性、不同增强之间的差异、uncertainty 或 disagreement，但这些不是正式 mIoU。

### 8. 为什么 blocky 类 IoU 低？

blocky 类形状变化大，边界更不规则，和 simple 或背景更容易混淆。当前 blocky IoU 是 53.58%，说明它是下一步优化重点。后续可以补充更多 blocky 样本、做类别重采样、针对块状边界做增强，或者单独分析误检漏检案例。

### 9. fixed fusion 为什么会降低 mIoU？

fusion 的初衷是让预测更稳定，但对小病害来说，多次预测取融合可能会把细小前景平均掉或抹掉。实验里 fixed fused 在全部样本上 mIoU 是 0.3287，低于 single 的 0.3725，说明固定融合并不总是适合这个任务。

### 10. selected 为什么没有始终超过 single，还能说有价值吗？

可以，但要准确表达。selected 在全部样本上比 single 低 0.0044，所以不能说它全面超过 single。它的价值是避免 fixed fusion 的明显损失，在全部样本上相对 fixed fused 提升 0.0393，并且能提供复核和解释证据。

### 11. uncertainty 是什么？

uncertainty 表示模型对某些像素不太确定。当前 Web 里的 uncertainty 来自 SegFormer 多姿态概率融合后的熵，不是手工画出来的图。它不直接等于错误，但可以提示哪些区域值得人工复核。在有 GT 的样本里，高 uncertainty 区域中有 78.27% 是错误像素，说明它对找风险区域有参考意义。

### 12. 没有 GT 的上传图片为什么还显示一些指标？

上传图片可以显示模型自身输出相关的指标，比如 mask 面积、连通域数量、骨架长度、不同候选 mask 的差异、自一致性和复核提示。但不能显示真实 mIoU，因为没有人工标注作为标准答案。

### 13. 骨架图有什么意义？

骨架图把 mask 的形状压缩成中心线，方便观察病害的方向、长度和连通结构。对于裂缝类或长条形病害，骨架比整块 mask 更容易看出走势。它是解释证据，不是替代分割结果。

### 14. 风险提示能不能代表结构安全结论？

不能。当前风险提示是图像检测层面的复核建议，意思是“这块预测值得人工再看”。它不能直接等同于结构安全等级，更不能替代工程检测结论。

### 15. 小病害保护的 1,230,616 个像素是什么意思？

它表示相比 fixed fusion，被自适应选择保留下来的前景像素总量。可以理解为 fixed fusion 可能抹掉的一部分病害证据，被 selected 策略在很多样本里保住了。

### 16. 78.27% HU error precision 怎么解释？

HU 是 high uncertainty。HU error precision 表示高不确定性像素中，有多少比例确实是预测错误。78.27% 说明高 uncertainty 区域很大程度上对应错误区域，因此适合作为人工复核提示。

现在 U6 里还补了 calibration evidence，也就是把 uncertainty 分桶，比较每个桶里的平均不确定性和真实错误率差距。这个可以通俗理解成：模型说“我不确定”的地方，实际是不是更容易错。

### 17. 这个系统现在最大的不足是什么？

第一，blocky 类还弱，IoU 只有 53.58%。第二，selected 的目标是减少 fixed fusion 损失，不是保证超过 single。第三，自选图片没有 GT，无法给真实精度。第四，当前风险提示还停留在图像层面，没有和工程结构安全标准直接绑定。

### 18. 如果老师说“这只是后处理”，怎么回答？

可以说：它确实是在模型输出之后做增强，但它解决的是实际使用中的关键问题。很多工程系统不是只需要最高模型分数，还需要知道什么时候不要融合、什么时候要复核、怎么把 mask 变成可解释证据。这是面向应用和专利更有差异化的部分。

### 19. 专利可以写哪些权利要求？

可以围绕五点写：候选 mask 的自适应选择方法；检测 fixed fusion 抹掉小病害的保护策略；基于 mask 的骨架、连通域、面积、方向和风险证据生成；置信校准和人工复核优先级生成；无 GT 场景下的可信展示和复核提示机制。

### 20. 下一步最应该做什么？

 优先做两件事。第一，针对 blocky 类继续优化训练和数据增强，因为它是当前短板。第二，围绕 `experiments/patent_evidence_test_pack.json` 继续整理 3 到 5 个典型案例，分别展示 fixed fusion 失败、selected 保留小病害、uncertainty 提示复核、review queue 排序和 morphology delta 解释，这些案例可以直接用于论文、答辩和专利附图。

### 21. 多领域扩展现在到什么程度？

civil 主线已经完成，能跑 SegFormer、enhancement、report export 和 Web 展示。track 和 equipment 目前先用 demo adapter 接入，统一到同一份 multidomain schema 里，目的是先让比赛展示能看懂、能扩展，再逐步替换成真实训练模型。spatial mapping 目前还应该按 planned / prototype 讲，不能夸大成外业级定位。

## Key Parameters And Methods

| Term | Type | Slide(s) | Definition |
|---|---|---:|---|
| GT | 数据标注 | 3, 4, 10 | Ground Truth，人工标注的标准 mask；训练和真实 mIoU 评估都需要它。 |
| mask | 输出形式 | 1-13 | 每个像素的类别预测图，用来表示病害区域。 |
| SegFormer B1 | 模型 | 5 | 基础语义分割模型，用来提高病害 mask 的质量。 |
| mIoU | 指标 | 5, 8, 10 | mean Intersection over Union，各类别 IoU 的平均值，需要预测 mask 和 GT 对比。 |
| IoU | 指标 | 5 | 预测区域和 GT 区域交集除以并集。 |
| aAcc | 指标 | 5 | all pixel accuracy，整体像素准确率。 |
| single mask | 候选结果 | 6, 8 | 单次模型推理得到的 mask。 |
| fixed fused | 对照策略 | 6, 8, 9 | 固定融合多个候选概率预测，可能提高稳定性，也可能抹掉小病害。 |
| selected mask | 增强输出 | 6-10 | 自适应选择后的最终 mask。 |
| uncertainty | 复核信号 | 9, 10 | 由 SegFormer 概率 TTA 计算出的模型不确定区域，用于提示人工复核。 |
| skeleton | 形态证据 | 1, 10 | 从 mask 提取的骨架线，用来观察方向、长度和连通结构。 |
| blocky | 类别 | 3, 5, 13 | 块状病害类别，当前 IoU 最低，是后续优化重点。 |

## Timing Table

| Slide | Title | Suggested Time |
|---:|---|---:|
| 1 | 给隧道图片自动找病害 | 0:45 |
| 2 | 问题不只是能不能画出来 | 0:55 |
| 3 | 数据集和类别标准 | 0:50 |
| 4 | 系统流程 | 0:55 |
| 5 | SegFormer 提升基础分割质量 | 1:10 |
| 6 | 自适应增强与复核优先级模块 | 1:00 |
| 7 | 创新点 | 1:15 |
| 8 | 实验结果 | 1:10 |
| 9 | 具体价值 | 1:00 |
| 10 | Web 展示 | 1:00 |
| 11 | 多领域扩展 | 0:55 |
| 12 | 专利方向 | 1:00 |
| 13 | 总结和下一步 | 0:50 |

总时长约 12-13 分钟。时间紧时，可以压缩第 3、4 页，把重点留给第 5、7、8、10、11、12 页。

## One-Minute Backup Summary

这个项目做的是隧道病害语义分割和可信增强展示。基础模型部分，我把系统切换到 SegFormer B1，在 6 类数据集上训练到 160000 iter，最终 mIoU 达到 84.33%。增强部分，我没有固定采用融合结果，而是用同一个 SegFormer checkpoint 生成 single 和 probability TTA fused 结果，比较候选 mask 的稳定性；当 fixed fusion 可能抹掉小病害时，selected 策略保留更可靠的输出。系统还会从模型概率里生成 uncertainty 和 disagreement，并从 mask 中提取骨架、面积、方向和连通域。U6 又增加了 uncertainty calibration evidence 和 review priority：有 GT 时看 uncertainty 是否真的对应错误，没有 GT 时只给人工复核优先级，不乱报真实精度。后面又补上了多领域扩展，把 civil 主线和 track / equipment 的 demo adapter 接到同一份结果合同里，方便比赛展示、专利表达和后续软著整理。最后做成 Web demo，支持拖拽图片实时检测。创新点主要是病害分割后的自适应输出选择、置信校准和复核优先级生成流程。
