# 隧道病害分割结果可信复核方法权利要求草稿

## 说明

本文为研发侧权利要求草稿，旨在将当前算法链转化为可供讨论的专利表达。正式申请前须由专利代理人根据查新结果、现有技术和保护范围进行法律化改写。

核心保护对象为 post-inference confidence review 方法，而非 SegFormer、TTA、entropy uncertainty、skeletonization 或 mIoU 指标本身。

## 独立权利要求 1：方法

1. 一种隧道病害分割结果可信复核方法，其特征在于，包括：

   1. 获取隧道巡检图像，并通过语义分割模型生成至少一个候选病害分割结果；
   2. 对同一隧道巡检图像执行至少一种推理姿态或扰动，得到多组候选类别概率图或候选分割掩膜；
   3. 将所述多组候选类别概率图或候选分割掩膜对齐到原图坐标系，并生成融合分割候选结果；
   4. 比较所述单次分割候选结果与所述融合分割候选结果之间在前景面积、一致性和前景保护像素方面的差异，以识别固定融合对病害区域的压缩或丢失；
   5. 根据融合损伤识别结果、候选一致性、不确定性和分歧度，从多个候选分割结果中确定 selected mask；
   6. 基于所述候选类别概率图或候选分割掩膜计算 uncertainty 和 disagreement，用于定位需要人工复核的区域；
   7. 对所述单次分割候选结果、融合分割候选结果和 selected mask 进行形态变化分析，得到 morphology_delta；
   8. 综合所述融合损伤识别结果、uncertainty、disagreement、morphology_delta 和 selected mask 的选择理由，生成 review priority 和复核队列。

## 从属权利要求：方法细化

2. 根据权利要求 1 所述的方法，其中所述推理姿态包括原图推理和水平翻转推理，所述水平翻转推理结果经反变换后与原图推理结果对齐。

3. 根据权利要求 1 所述的方法，其中所述固定融合对病害区域的压缩或丢失通过前景面积收缩比例、protected pixels、single/fused self-consistency 或 selected-vs-fused 差异中的至少一种确定。

4. 根据权利要求 1 所述的方法，其中当融合分割候选结果相对于单次分割候选结果存在小病害前景收缩时，所述 selected mask 选择单次分割候选结果或混合候选结果，以保护被融合抑制的病害前景。

5. 根据权利要求 1 所述的方法，其中所述 uncertainty 包括基于类别概率分布的信息熵，所述 disagreement 包括不同推理姿态或候选分割结果之间的类别差异。

6. 根据权利要求 1 所述的方法，其中所述 morphology_delta 包括前景面积变化、连通域数量变化、骨架长度变化和主方向变化中的至少一种。

7. 根据权利要求 1 所述的方法，其中所述 review priority 根据图像风险、uncertainty、disagreement、候选 self-consistency、融合前景收缩、morphology_delta 和 selected mask 选择理由中的多项生成。

8. 根据权利要求 1 所述的方法，其中在不存在 ground-truth mask 的上传图片场景中，所述方法输出 self-consistency、uncertainty、disagreement、morphology_delta 和 review priority，而不输出 true mIoU、error overlap 或基于 ground-truth 的 calibration。

9. 根据权利要求 1 所述的方法，其中在存在 ground-truth mask 的数据集样本中，所述方法进一步统计 selected mask、融合分割候选结果和单次分割候选结果的 label-based 指标，并用于评估所述复核队列是否覆盖真实错误或固定融合损伤。

10. 根据权利要求 1 所述的方法，其中所述复核队列中的每个样本包括图像标识、review priority、触发理由、selected mask 选择模式、ground-truth 可用性和 artifact 可用性。

## 独立权利要求 11：系统

11. 一种隧道病害分割结果可信复核系统，其特征在于，包括：

   - 候选预测生成模块，用于获取隧道巡检图像并生成候选分割结果；
   - 概率对齐与融合模块，用于对多姿态推理结果进行坐标对齐并生成融合分割候选结果；
   - 融合损伤识别模块，用于比较单次分割候选结果和融合分割候选结果并识别病害前景收缩；
   - 自适应选择模块，用于从候选分割结果中确定 selected mask；
   - 不确定性与分歧分析模块，用于生成 uncertainty 和 disagreement；
   - 形态变化分析模块，用于生成 morphology_delta；
   - 复核排序模块，用于生成 review priority 和复核队列；
   - 证据导出模块，用于输出分割图、热力图、骨架图、结构化报告和证据汇总。

## 从属权利要求：系统细化

12. 根据权利要求 11 所述的系统，其中所述候选预测生成模块接入 SegFormer、FCN 或其他语义分割模型作为 mask source。

13. 根据权利要求 11 所述的系统，其中所述证据导出模块进一步输出 patent evidence pack，所述 patent evidence pack 包括 backbone evidence、enhancement evidence、representative cases、artifact path templates、artifact availability 和 claim boundaries。

14. 根据权利要求 11 所述的系统，其中所述系统通过 Web 界面展示原图、selected mask、overlay、uncertainty、disagreement、skeleton、morphology_delta 和 review priority，所述 Web 界面作为系统实施方式而非算法保护范围的唯一限定。

## 独立权利要求 15：计算机可读存储介质

15. 一种计算机可读存储介质，其上存储有计算机程序，所述计算机程序被处理器执行时实现权利要求 1 至 10 中任一项所述的隧道病害分割结果可信复核方法。

## 保护边界提示

本权利要求书的保护重点在于候选分割结果的可信复核与证据生成链条。以下内容不应作为独立发明点主张：

- SegFormer 网络结构本身。
- test-time augmentation 概念本身。
- entropy uncertainty 指标本身。
- skeletonization 算法本身。
- mIoU 指标本身。
- 结构安全诊断或最终养护决策。
