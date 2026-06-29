# 用户操作手册草案

软件名称：机器人隧道巡检病害时空分析与复检管理系统  
软件简称：隧道病害巡检分析系统  
版本号：V1.0

## 1. 软件安装准备

### 1.1 基础环境

用户需准备以下环境：

- Windows 10 / Windows 11；
- Python 3.8 及以上；
- Git 或其他代码管理工具；
- Microsoft Edge、Chrome 或其他现代浏览器。

### 1.2 Python 依赖

在项目根目录下安装轻量依赖：

```bash
pip install -r requirements.txt
```

如果使用 SegFormer / mmsegmentation 相关模型推理能力，需要单独准备对应深度学习环境。当前机器人巡检表格分析流程不强制依赖训练环境。

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

浏览器打开：

```text
http://127.0.0.1:8000/
```

如果图片或图表无法显示，可先确认 `outputs/visualizations/` 下存在 PNG 文件，并重启 Web 服务后刷新浏览器。

## 4. 查看系统总览

进入 Web Dashboard 后，系统总览区域会展示巡检次数、图像帧数量、病害对象数量、重点复检数量和高风险记录数量等信息。该区域用于快速了解当前 demo 数据的总体分析结果。

## 5. 查看工程报告

工程报告页面展示按巡检和病害对象整理后的工程化中文描述，包括病害类型、里程、环号、方位、面积和风险等级等信息。

## 6. 查看规则面积变化提示

规则面积变化提示页面展示同一病害对象在不同巡检记录中的面积变化、风险变化和关注等级。该结果是基于面积和风险规则生成的复检辅助提示，不等同于结构安全结论。

## 7. 查看重点复检清单

重点复检清单展示系统建议优先关注的病害对象。用户可查看病害编号、病害类型、里程位置、关注等级、复检原因和复检建议。

## 8. 查看可视化图表

可视化图表包括关注等级分布、病害类型分布、规则面积变化分布、里程段风险分布、面积变化率和关联关系图等。图表用于辅助理解当前 demo 数据的分布情况。

## 9. 单图检测 / 上传图片复核

单图检测入口用于对单张图片进行检测和复核展示。常见输出包括单次 mask、融合 mask、选择 mask、叠加图、不确定性图、分歧图和骨架图。若缺少人工 GT 标注，页面中的一致性指标不能作为真实分割精度。

## 10. 常见问题

### 10.1 Web 页面打不开

检查 `web_app.py` 是否正在运行，确认浏览器访问地址为：

```text
http://127.0.0.1:8000/
```

### 10.2 图表不显示

先运行：

```bash
python run.py --mode full_pipeline
```

然后重启 Web 服务并刷新浏览器。

### 10.3 Association 结果是否使用 disease_id

主流程使用 no-id 模式，`disease_id` 只作为标签和评估对照，不参与主流程匹配评分。with-id 结果仅作为渐进式评估的上界对照。

### 10.4 是否可以直接接入真实巡检视频

当前版本未提供完整的视频上传、抽帧、模型推理和任务队列流程。真实视频接入属于后续扩展方向。

## 11. 注意事项

1. 当前版本基于 KICT 静态图像 / mask 与仿真巡检元数据进行工程原型验证。
2. 当前规则面积变化提示只用于复检辅助，不是结构安全结论。
3. Web Dashboard 当前主要用于展示已生成的 demo 分析结果。
4. 使用真实数据前，需要补充真实巡检图像、病害标注、巡检元数据和跨巡检对应关系。
5. 输出报告可用于项目展示和材料整理，但正式工程应用前需要人工复核和专业检测流程确认。
