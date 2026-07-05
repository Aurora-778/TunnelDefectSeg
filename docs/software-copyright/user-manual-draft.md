# 用户操作手册草案

软件名称：机器人隧道巡检病害时空分析与复检管理系统  
软件简称：隧道病害巡检分析系统  
版本号：V1.0

## 0. 正式提交版格式要求

根据软著申请规范，正式整理为 Word / PDF 用户使用说明书时，建议按以下格式处理：

- 封面标题使用：`机器人隧道巡检病害时空分析与复检管理系统 用户使用说明书`。
- 封面和页眉不写版本号，页眉只放软件名称。
- 页码放在页面右上端，与页眉同一行。
- 正文章节可按“引言、系统概述、运行环境、系统说明、使用流程、注意事项”组织。
- 正式版至少包含一张图片，可插入 Web Dashboard 系统总览截图或软件处理流程图。
- 图片编号建议使用 `图2.1 系统总览界面`、`图2.2 重点复检清单界面` 这类格式。
- 字体、字号、段前段后和行距应按学校提供的用户说明书模板统一设置。

## 1. 软件安装准备

### 1.1 基础环境

用户需准备以下环境：

- Windows 10 / Windows 11；
- Python 3.8 及以上；
- Git 或其他代码管理工具；
- Microsoft Edge、Chrome 或其他现代浏览器。

### 1.2 Python 依赖

在项目根目录下安装基础分析依赖：

```bash
pip install -r requirements.txt
```

该依赖主要用于表格分析、报告生成和可视化产物生成。若需要生成或导出视频 demo 标注产物，需要 OpenCV 环境；若需要使用 Supervision 标注展示层，可按 `requirements-video.txt` 另行准备可选依赖。若需要启动 Web Dashboard 中的单图检测 / 上传图片复核能力，可能还需要准备 PyTorch、SegFormer / mmsegmentation 等模型推理相关环境。当前机器人巡检表格分析流程不强制依赖训练环境。

### 1.3 数据准备

当前版本默认使用仓库中已整理的 KICT 静态图像 / mask 几何特征和仿真巡检元数据。当前版本基于 KICT 静态图像 / mask 与仿真巡检元数据进行工程原型验证。

## 2. 运行完整 pipeline

在项目根目录执行：

```bash
python run.py --mode full_pipeline
```

执行完成后，系统会生成工程化病害报告、规则面积变化提示、Disease Memory Bank、Association 记录、重点复检清单、可视化图表和最终项目报告。

常见输出包括：

- `data/simulated/disease_engineering_report.csv`
- `data/simulated/disease_growth_results.csv`
- `data/simulated/disease_memory_bank.csv`
- `data/simulated/disease_association_records.csv`
- `data/simulated/priority_recheck_list.csv`
- `outputs/final_project_report.md`
- `outputs/visualizations/*.png`

## 3. 启动 Web Dashboard

在项目根目录执行：

```bash
python web_app.py --host 127.0.0.1 --port 8000
```

如果仅查看已生成的 demo 分析结果，应先确保完整 pipeline 已生成对应 CSV、Markdown 和 PNG 产物。若使用单图检测或上传图片复核功能，需要当前 Python 环境能够加载项目中的模型推理依赖。

浏览器打开：

```text
http://127.0.0.1:8000/
```

如果图片或图表无法显示，可先确认 `outputs/visualizations/` 下存在 PNG 文件，并重启 Web 服务后刷新浏览器。

## 4. 运行视频 demo 展示闭环

当前版本提供本地视频 demo 展示流程。该流程使用 KICT 静态图像 / mask 合成 `tunnel_demo.mp4`，并生成视频抽帧、视频巡检元数据、视频病害几何特征、OpenCV 标注帧、OpenCV 标注视频和可选 Supervision 标注视频。

Windows 用户可在项目根目录双击：

```text
run_demo_showcase.bat
```

命令行也可执行：

```bash
python scripts/run_demo_showcase.py --video_id tunnel_demo
```

如果当前环境没有安装 Supervision，可使用：

```bash
python scripts/run_demo_showcase.py --video_id tunnel_demo --skip_supervision
```

运行后可执行以下命令检查视频 demo 产物是否完整：

```bash
python scripts/validate_video_artifacts.py --video_id tunnel_demo
```

常见视频输出包括：

- `data/videos/tunnel_demo.mp4`
- `data/video_frames/tunnel_demo/frames_manifest.csv`
- `data/video_inspection/tunnel_demo/metadata.csv`
- `data/video_inspection/tunnel_demo/disease_features.csv`
- `outputs/video_inspection/tunnel_demo/annotated_video.mp4`
- `outputs/video_inspection/tunnel_demo/supervision_annotated_video.mp4`：仅在启用 Supervision 可选视觉层且未使用 `--skip_supervision` 时生成

需要注意：该视频由 KICT 静态图像 / mask 合成，用于演示视频输入和可视化展示流程，不是真实机器人连续巡检视频，也不是实时视频流分析。

## 5. 查看系统总览

进入 Web Dashboard 后，系统总览区域会展示巡检次数、图像帧数量、病害对象数量、重点复检数量和高风险记录数量等信息。该区域用于快速了解当前 demo 数据的总体分析结果。

## 6. 查看工程报告

工程报告页面展示按巡检和病害对象整理后的工程化中文描述，包括病害类型、里程、环号、方位、面积和风险等级等信息。

## 7. 查看规则面积变化提示

规则面积变化提示页面展示同一病害对象在不同巡检记录中的面积变化、风险变化和关注等级。该结果是基于面积和风险规则生成的复检辅助提示，不等同于结构安全结论。

## 8. 查看重点复检清单

重点复检清单展示系统建议优先关注的病害对象。用户可查看病害编号、病害类型、里程位置、关注等级、复检原因和复检建议。

## 9. 查看可视化图表

可视化图表包括关注等级分布、病害类型分布、规则面积变化分布、里程段风险分布、面积变化率和关联关系图等。图表用于辅助理解当前 demo 数据的分布情况。

## 10. 查看视频分析结果

启动 Web Dashboard 后，可进入视频分析结果页面查看：

- demo video；
- OpenCV 标注视频；
- Supervision 可选标注视频；
- `disease_features.csv` 表格；
- `inspection_sequence.csv` 表格；
- 视频可视化 manifest 表格。

如果页面提示“尚未生成该视频分析产物”，应先运行 `run_demo_showcase.bat` 或 `python scripts/run_demo_showcase.py --video_id tunnel_demo`。

## 11. 单图检测 / 上传图片复核

单图检测入口用于对单张图片进行检测和复核展示。常见输出包括单次 mask、融合 mask、选择 mask、叠加图、不确定性图、分歧图和骨架图。若缺少人工 GT 标注，页面中的一致性指标不能作为真实分割精度。

## 12. 常见问题

### 12.1 Web 页面打不开

检查 `web_app.py` 是否正在运行，确认浏览器访问地址为：

```text
http://127.0.0.1:8000/
```

### 12.2 图表不显示

先运行：

```bash
python run.py --mode full_pipeline
```

然后重启 Web 服务并刷新浏览器。

### 12.3 Association 结果是否使用 disease_id

主流程使用 no-id 模式，`disease_id` 只作为标签和评估对照，不参与主流程匹配评分。with-id 结果仅作为渐进式评估的上界对照。

### 12.4 视频 demo 是否等同于真实机器人巡检视频

不是。当前 `tunnel_demo.mp4` 由 KICT 静态图像 / mask 合成，只用于验证视频输入处理、标注视频导出和 Web 展示流程。

### 12.5 是否可以直接接入真实巡检视频

当前版本已提供本地 demo video 生成、抽帧、已有 mask 几何特征提取、标注视频导出和视频产物展示能力，但未提供完整的视频上传、模型自动推理、任务队列和实时视频流分析流程。真实视频接入属于后续扩展方向。

## 13. 注意事项

1. 当前版本基于 KICT 静态图像 / mask 与仿真巡检元数据进行工程原型验证。
2. 当前规则面积变化提示只用于复检辅助，不是结构安全结论。
3. 视频 demo 由 KICT 静态图像 / mask 合成，不是真实机器人连续巡检视频。
4. Web Dashboard 当前主要用于展示已生成的 demo 分析结果。
5. Supervision 只是可选标注展示层，不参与 Disease Memory Bank、no-id Association 和规则面积变化提示等核心分析逻辑。
6. 使用真实数据前，需要补充真实巡检图像、病害标注、巡检元数据和跨巡检对应关系。
7. 输出报告可用于项目展示和材料整理，但正式工程应用前需要人工复核和专业检测流程确认。
