# Technical Report Outline

这份大纲用于比赛技术报告、软著材料和老师阶段检查。它不是最终成稿，而是把已有实验和新加的多领域扩展串成一条可写、可讲、可引用的主线。

## 1. Title And Abstract

- 项目名称：隧道病害智能分割与可信复核分析系统
- 报告目标：说明系统如何从“只会画 mask”升级到“能解释、能复核、能扩展到多领域巡检”
- 摘要重点：
  - SegFormer B1 作为当前基础 mask source
  - 自适应 selection 解决 fixed fusion 抹掉小病害的问题
  - review priority / uncertainty / morphology 组成可信复核链
  - civil / track / equipment 统一结果 schema 已接入

## 2. Problem Background

- 隧道病害巡检的工程痛点
- 只给 mask 不够，需要工程语言和复核证据
- 没有 GT 的现场图片不能乱报真实 mIoU
- 比赛不仅看算法分数，也看系统闭环和可展示性

## 3. Data And Task Definition

- 1000 张样本，700 / 150 / 150 划分
- 6 类土建标签定义
- GT mask 的作用：训练监督、真实性能评估
- 自选上传图片的边界：无 GT，仅输出模型证据

## 4. Backbone Model

- SegFormer B1 作为主干
- 160000 iter 训练结果：mIoU 84.33%，mAcc 91.17%，aAcc 98.62%
- 类别瓶颈：blocky IoU 最低
- 说明 backbone 是基础能力，不是最终创新点

## 5. Confidence Review Pipeline

- single / fused / hybrid 候选生成
- fixed fusion 可能抹掉小病害
- adaptive selection 如何决定最终输出
- uncertainty / disagreement 的含义和边界
- review priority 的组成、解释和用途

## 6. Morphology And Evidence Generation

- 面积、连通域、方向、骨架长度的作用
- morphology delta 解释 single/fused/selected 的变化
- 为什么骨架图比单纯颜色块更适合讲病害形态

## 7. Multidomain Extension

- civil / track / equipment 统一结果 schema
- civil 结果如何从当前 6 类标签映射到比赛口径
- track / equipment demo adapter 如何保持 `simulation-only` 边界
- spatial mapping 已有 prototype 实现，但仍按 simulation/calibration 边界说明

## 8. Experiments

- backbone 指标
- selected vs fused 的恢复效果
- review queue / HU error precision
- demo case pack 的 C1-C7 证据结构
- 证据来源标注规则：internal dataset / simulation / pending / official samples

## 9. Web Demo And Report Export

- 拖拽图片实时检测
- 多视图展示：原图、mask、overlay、uncertainty、disagreement、skeleton
- structured report 的字段设计
- multidomain summary 的展示方式

## 10. Innovation And Patent Angle

- 固定融合损害检测
- 自适应输出选择
- 复核优先级生成
- 形态变化证据生成
- 无 GT 场景下的可信展示边界

## 11. Limitations And Future Work

- blocky 类仍是弱项
- 轨道/设备目前是 demo adapter，不是完整训练模型
- spatial mapping 现在有 prototype 实现，但仍不是外业级定位
- 真实官方比赛样本还未接入

## 12. Conclusion

- 系统已经具备“能训练、能评估、能展示、能解释”的闭环
- 目前最适合继续推进的是：
  - blocky 类优化
  - 多领域样本补充
  - spatial mapping 原型与边界说明
  - 典型案例和材料包完善

