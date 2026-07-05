# 软件功能说明书

软件名称：机器人隧道巡检病害时空分析与复检管理系统  
软件简称：隧道病害巡检分析系统  
版本号：V1.0

## 1. 软件概述

本软件面向机器人隧道巡检病害分析场景，提供从病害图像几何信息接入、巡检元数据融合、工程化病害描述、病害对象记忆、规则关联分析、规则面积变化提示、重点复检清单生成、视频 demo 可视化展示到 Web Dashboard 展示的完整工程原型流程。

当前版本基于 KICT 静态图像 / mask 与仿真巡检元数据进行工程原型验证。软件的重点是验证数据结构、处理流程、分析结果组织和展示能力，不将当前结果描述为真实线路病害长期演化结论。

## 2. 软件设计目标

1. 将隧道病害图像 mask 的面积、bbox、中心点等几何特征整理为结构化数据。
2. 将病害图像信息与巡检时间、里程、环号、方位等工程元数据关联。
3. 生成面向工程阅读的病害描述、风险等级和复检建议。
4. 建立 Disease Memory Bank，用于按病害对象汇总记录。
5. 使用 no-id Association 规则进行病害对象与巡检帧的关联分析。
6. 根据面积和风险变化生成规则面积变化提示。
7. 生成重点复检清单和可视化图表。
8. 支持由 KICT 静态图像 / mask 合成 demo video，生成视频帧元数据、mask 几何特征、标注帧和标注视频。
9. 提供视频产物校验和一键演示脚本，便于演示前检查产物完整性。
10. 通过本地 Web Dashboard 展示分析结果。

## 3. 软件运行环境

- 操作系统：Windows 10 / Windows 11，兼容常见 Linux Python 环境
- Python：3.8 及以上
- 浏览器：Microsoft Edge、Chrome 或其他现代浏览器
- 数据文件：CSV、JSON、Markdown、PNG、JPG、MP4
- 推荐硬件：普通 PC 可运行表格分析、报告生成、可视化产物生成和已生成结果展示；涉及模型推理或训练相关功能时建议使用具备 NVIDIA GPU 的设备
- 可选视频展示依赖：OpenCV 用于视频读写和标注视频导出；Supervision 可作为可选标注展示层，不属于核心分析算法

## 4. 软件总体架构

软件采用“数据输入层、数据处理层、分析决策层、报告输出层、Web 展示层”的结构。

1. 数据输入层：读取 KICT 图像 / mask 几何特征和仿真巡检元数据。
2. 数据处理层：完成数据校验、特征提取、字段合并和统一 CSV 产物生成。
3. 分析决策层：生成工程化病害报告、Disease Memory Bank、Association 记录、规则面积变化提示和重点复检清单。
4. 报告输出层：生成 Markdown 报告、CSV 表格和 PNG 图表。
5. 视频展示层：生成 demo video、抽帧结果、视频巡检元数据、标注帧、OpenCV 标注视频和可选 Supervision 标注视频。
6. Web 展示层：通过本地 Web Dashboard 展示系统总览、工程报告、规则面积变化提示、复检清单、可视化图表、单图复核结果和视频分析结果。

## 5. 数据输入说明

当前版本的主要输入包括：

- KICT 静态裂缝图像和 mask；
- 仿真巡检序列表；
- 仿真病害对象表；
- 图像帧与病害对象映射表；
- KICT mask 几何特征表；
- 融合后的机器人巡检核心表；
- 由 KICT 静态图像 / mask 合成的 demo video 及其视频帧、mask 和视频巡检表。

其中，巡检时间、里程、环号、方位、`disease_id` 和跨巡检关系来自仿真元数据。后续接入真实巡检数据时，应补充真实巡检图像或视频帧、病害标注、巡检元数据和跨巡检对应关系。

## 6. 主要功能模块

1. 数据检查模块：检查图像和 mask 文件是否匹配。
2. 几何特征提取模块：提取 mask 面积、bbox、中心点和尺寸。
3. 仿真巡检表生成模块：生成巡检序列、病害实例和增长记录。
4. 数据融合模块：将 KICT 几何特征接入仿真巡检表。
5. 工程报告模块：生成按巡检和病害对象组织的中文工程描述。
6. Disease Memory Bank 模块：生成病害对象记忆表。
7. Association 模块：基于空间、面积、时间和风险规则生成 no-id 关联记录。
8. 规则面积变化提示模块：根据面积和风险变化生成提示。
9. 重点复检模块：输出重点关注病害和复检建议。
10. 可视化模块：生成风险、病害类型、面积变化和里程分布图表。
11. 视频 demo 模块：合成 demo video、抽取视频帧、生成视频巡检元数据、提取视频帧 mask 几何特征。
12. 视频标注展示模块：生成 OpenCV 标注帧、OpenCV 标注视频、Supervision 可选标注帧和标注视频。
13. 视频产物校验模块：检查 demo video、视频帧、视频巡检 CSV、标注帧和标注视频是否完整。
14. Web Dashboard 模块：提供本地展示界面。
15. 最终报告模块：生成系统摘要、关键洞察和最终项目报告。

## 7. 模块详细说明

### 7.1 数据检查模块

该模块扫描图像目录和 mask 目录，按文件名 stem 匹配图像与标注，并可生成预览图。该模块用于确认输入数据是否满足后续处理要求。

### 7.2 几何特征提取模块

该模块读取 mask 文件，将非零像素视为病害区域，计算病害面积、外接矩形、中心点、mask 宽高等字段。若 mask 为空，则输出空病害区域的占位值。

### 7.3 工程报告模块

该模块根据巡检编号、里程、环号、方位、病害类型和面积信息生成工程化中文描述，便于在 Web Dashboard 或报告材料中直接查看。

### 7.4 Disease Memory Bank 模块

该模块按病害对象汇总历史记录，输出 `memory_id`、病害类型、面积信息、巡检次数、关注等级、人工复核状态和文字描述。当前主流程为批处理记忆表，渐进式评估流程支持增量更新路径。

### 7.5 Association 模块

该模块使用空间距离、面积相似度、巡检时间连续性和风险相似度等非 ID 规则证据进行关联评分。主流程固定采用 no-id 模式，`disease_id` 只作为标签和评估对照，不参与主流程匹配评分。

### 7.6 规则面积变化提示模块

该模块根据不同巡检记录中的面积和风险等级变化，生成“基本稳定”“面积减小”等规则提示。该提示用于复检辅助，不构成结构安全结论。

### 7.7 Web Dashboard 模块

该模块通过本地 Web 页面展示系统总览、工程报告、规则面积变化提示、重点复检清单、可视化图表、单图检测 / 复核结果和视频分析结果。当前 Web 主要用于展示已经生成的 demo 分析结果。

### 7.8 视频 demo 与标注展示模块

该模块基于 KICT 静态图像 / mask 合成 `tunnel_demo.mp4`，并将视频抽帧结果整理为视频巡检元数据表、视频病害几何特征表和视频巡检序列表。随后可生成 OpenCV 标注帧、OpenCV 标注视频、Supervision 可选标注帧和 Supervision 标注视频，用于展示病害位置、bbox、mask 轮廓、风险等级和面积信息。该模块用于演示视频输入和结果展示流程，不表示已经完成真实机器人视频上传、实时视频流分析或模型自动推理闭环。

### 7.9 视频产物校验模块

该模块检查 demo video、源帧目录、视频巡检 CSV、OpenCV 标注帧、OpenCV 标注视频、Supervision 可选标注产物等文件是否完整，并校验关键 CSV 的 `video_id` 或 `inspection_id` 关系。当前默认对 demo 数据保持严格校验，同时保留非 demo 巡检编号兼容开关。

## 8. 数据处理流程

```text
KICT 图像 / mask
        ↓
mask 几何特征提取
        ↓
仿真巡检元数据生成
        ↓
KICT 特征与巡检表融合
        ↓
工程化病害描述
        ↓
Disease Memory Bank
        ↓
no-id Association
        ↓
规则面积变化提示
        ↓
重点复检清单和可视化图表
        ↓
Web Dashboard 与最终报告
```

视频 demo 展示流程如下：

```text
KICT 静态图像 / mask
        ↓
合成 demo video
        ↓
视频抽帧与视频巡检元数据生成
        ↓
已有 mask 几何特征提取
        ↓
OpenCV / Supervision 标注帧与标注视频导出
        ↓
视频产物完整性校验
        ↓
Web Dashboard 视频分析结果展示
```

## 9. Web Dashboard 展示功能

Web Dashboard 支持以下展示功能：

- 系统总览指标；
- 工程化病害报告；
- 规则面积变化提示；
- 重点复检清单；
- 可视化图表；
- 单图检测和现场复核入口；
- 视频分析结果页面，包括 demo video、OpenCV 标注视频、Supervision 标注视频和相关 CSV 表格；
- KICT 静态数据与仿真巡检元数据边界说明。

当前 Web Dashboard 为本地演示和结果查看界面，后续可扩展为上传数据、异步分析和任务状态查看流程。

## 10. 输出结果说明

主要输出包括：

- `disease_engineering_report.csv`：工程化病害描述表；
- `disease_growth_results.csv`：规则面积变化提示表；
- `disease_memory_bank.csv`：病害对象记忆表；
- `disease_association_records.csv`：病害关联记录表；
- `priority_recheck_list.csv`：重点复检清单；
- `outputs/visualizations/*.png`：可视化图表；
- `outputs/final_project_report.md`：最终项目报告；
- `outputs/system_summary.md`：系统摘要；
- `outputs/key_insights.md`：关键洞察摘要。
- `data/videos/tunnel_demo.mp4`：由 KICT 静态图像 / mask 合成的 demo video；
- `data/video_frames/<video_id>/frames_manifest.csv`：视频抽帧清单；
- `data/video_inspection/<video_id>/metadata.csv`：视频巡检元数据表；
- `data/video_inspection/<video_id>/disease_features.csv`：视频帧病害几何特征表；
- `outputs/video_inspection/<video_id>/annotated_video.mp4`：OpenCV 标注视频；
- `outputs/video_inspection/<video_id>/supervision_annotated_video.mp4`：Supervision 可选标注视频；
- `outputs/video_inspection/<video_id>/video_visualization_manifest.csv`：视频可视化产物清单。

## 11. 软件特点

1. 支持从图像 mask 几何信息到工程报告的端到端处理。
2. 使用结构化 CSV / JSON / Markdown / PNG 产物，便于审查和复用。
3. 主流程采用 no-id 关联规则，避免把评估标签作为匹配依据。
4. 输出复检建议和人工复核字段，强调辅助分析与人工确认。
5. 提供视频 demo 生成、标注视频导出和视频产物校验能力，便于答辩、软著和专利材料展示。
6. 通过本地 Web Dashboard 提供可视化展示。

## 12. 应用场景

- 隧道巡检病害数据分析原型验证；
- 课程项目、科研项目和比赛材料展示；
- 机器人巡检数据处理流程设计；
- 病害复检清单和工程报告生成辅助；
- 后续真实巡检数据接入前的流程验证。

## 13. 当前版本边界与限制

当前版本基于 KICT 静态图像 / mask 与仿真巡检元数据进行工程原型验证。当前结果不能直接证明真实线路病害长期演化规律，不能直接作为现场工程检测结论，也不能用于替代人工确认和专业工程验收。真实应用前仍需接入真实机器人巡检数据、真实位姿或里程信息、人工核验结果和跨巡检对应关系。

当前视频 demo 由 KICT 静态图像 / mask 合成，用于验证视频输入处理、标注帧生成、标注视频导出和 Web 展示流程。该视频不是现场机器人连续巡检视频，当前系统也不是实时视频流分析系统。Supervision 仅作为可选可视化工具层，不参与 Disease Memory Bank、no-id Association 和规则面积变化提示等核心分析逻辑。

## 14. 软件功能和技术特点模板摘录

若需要填写学校模板中的“软件功能和技术特点”，可使用以下精简信息：

| 项目 | 建议填写内容 |
|---|---|
| 硬件环境 | 普通 PC 可运行表格分析、报告生成、可视化产物生成和已生成结果展示；模型推理或训练相关功能建议使用具备 NVIDIA GPU 的设备。 |
| 软件环境 | Windows 10 / Windows 11，Python 3.8 及以上，Microsoft Edge / Chrome 等现代浏览器；基础分析依赖包括 pandas、NumPy、Pillow、Matplotlib。 |
| 编程语言 | Python、HTML、CSS、JavaScript。 |
| 源程序量 | 核心 Python 源码约 10000 行，正式申请时以最终整理的源程序页为准。 |
| 主要功能和技术特点 | 软件将 KICT 静态图像 / mask 几何特征与仿真机器人巡检元数据整理为统一数据表，生成工程化病害描述、Disease Memory Bank、no-id Association 记录、规则面积变化提示、重点复检清单、可视化图表、视频 demo 标注产物和本地 Web Dashboard 展示结果。 |

以上内容用于软著材料填写，不应扩展为真实现场长期预测或工程验收结论。
