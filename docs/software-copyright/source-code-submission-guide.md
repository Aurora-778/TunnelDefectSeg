# 源代码提交材料建议

## 1. 提交目标

软件著作权申请通常需要提交源程序连续页和软件说明文档。源代码材料应体现软件的核心功能、模块结构和原创实现，避免提交大模型权重、数据集、缓存、日志和本地临时文件。

## 2. 建议选择的源代码代表文件

建议优先选择以下文件作为源代码代表：

| 文件 / 目录 | 选择理由 |
|---|---|
| `run.py` | 一键运行完整 pipeline 的入口，体现软件整体调度逻辑。 |
| `web_app.py` | 本地 Web 服务入口，体现数据接口、静态资源和单图复核能力。 |
| `orchestrator/agents/engineering_report_agent.py` | 工程化病害报告生成模块。 |
| `orchestrator/agents/memory_agent.py` | Disease Memory Bank 生成与更新模块。 |
| `orchestrator/agents/association_agent.py` | no-id Association 规则评分模块。 |
| `orchestrator/agents/visualization_agent.py` | 可视化和复检相关输出模块。 |
| `orchestrator/agents/final_report_agent.py` | 最终报告、系统摘要和关键洞察生成模块。 |
| `scripts/extract_kict_mask_features.py` | mask 几何特征提取逻辑。 |
| `scripts/merge_kict_with_simulation.py` | KICT 特征与仿真巡检元数据融合逻辑。 |
| `scripts/generate_engineering_report.py` | 工程化中文病害描述生成逻辑。 |
| `scripts/analyze_disease_growth.py` | 规则面积变化提示生成逻辑。 |
| `scripts/generate_visualization_and_recheck_list.py` | 图表与重点复检清单生成逻辑。 |
| `robot_sequence.py` | 连续巡检帧与路线元数据记录。 |
| `spatiotemporal_monitoring.py` | 路线级观测、defect track 关联和趋势声明门控。 |
| `robot_inspection_report.py` | 路线级 JSON 报告、复检队列和 claim guard 输出。 |
| `scripts/prepare_real_inspection_pilot.py` | 单 sequence 真实巡检图像、已有 mask 与元数据的数据合同、完整性清单和就绪性校验。 |
| `scripts/run_inspection_workflow.py` | 受控巡检工作流的固定 CLI 边界。 |
| `orchestrator/inspection_workflow/contracts.py` | 任务请求、固定输入输出及路径约束。 |
| `orchestrator/inspection_workflow/controller.py` | 受控运行的任务状态事件与完成门控。 |
| `orchestrator/inspection_workflow/lifecycle.py` | 输入快照、固定 DAG 执行、恢复检查和发布串联。 |
| `orchestrator/state/store.py` | 状态快照、版本控制、操作日志和恢复一致性校验。 |
| `scripts/create_demo_tunnel_video_from_kict.py` | KICT demo video 合成逻辑，体现视频展示支路的数据准备能力。 |
| `scripts/run_video_inspection_pipeline.py` | 视频抽帧、视频巡检元数据和视频 mask 特征串联逻辑。 |
| `scripts/annotate_video_frames.py` | OpenCV 标注帧生成逻辑。 |
| `scripts/export_annotated_video.py` | 标注视频导出逻辑。 |
| `scripts/validate_video_artifacts.py` | 视频 demo 产物完整性校验逻辑。 |
| `scripts/run_demo_showcase.py` | 本地视频 demo 展示闭环一键串联逻辑。 |

若需要提交前后连续页，可从上述文件中选择连续代码页，重点覆盖入口、数据处理、核心分析、视频 demo 展示和 Web 展示接口。

## 3. 不建议作为主要提交材料的内容

以下内容不建议作为软著主要源代码提交材料：

- 模型权重文件，例如 `.pth`、`.pt`、`.onnx`；
- 数据集原图、mask 和压缩包；
- `outputs/` 下生成的报告、图表和中间结果；
- `data/videos/`、`data/video_frames/`、`data/video_inspection/`、`data/video_masks/`、`outputs/video_inspection/` 下生成的视频、抽帧图片、CSV 和标注视频产物；
- `runs/`、`logs/`、`orchestrator/state/` 下的运行状态、操作日志、恢复标志和发布产物；应提交相应的程序源码，而非某次运行留下的证据文件；
- `experiments/` 下的大型实验产物；
- `__pycache__/`、`.pytest_cache/`、日志文件；
- `.codegraph/`、`.codebase-memory/` 等本地索引；
- 个人环境路径、临时目录、下载缓存；
- 第三方库源码或外部框架源码。

## 4. 源代码版式建议

根据申请规范，源代码正式提交版建议按以下方式整理：

```text
封面标题：机器人隧道巡检病害时空分析与复检管理系统 源代码
页眉内容：机器人隧道巡检病害时空分析与复检管理系统
页码位置：页面右上端，与页眉同一行
```

封面和页眉不写版本号，版本号保留在登记申请信息或正文说明中。源代码材料不需要目录。

若总程序不满 60 页，第一页标题写：

```text
源程序
```

若总程序超过 60 页，第一页标题写：

```text
源程序前30页
```

第 31 页标题写：

```text
源程序后30页
```

超过 60 页时应保证最后一页为满页，避免末页只有少量代码。源代码正文中可以在每段代码前标注仓库相对路径，例如 `orchestrator/agents/association_agent.py`，但不应使用本机绝对路径。

源代码正文中不建议额外修改大量注释或插入申请说明，以免影响代码真实性和连续性。

## 5. 路径与隐私处理建议

1. 避免在提交材料中出现个人用户名、桌面路径、下载路径等绝对路径。
2. 如代码或配置中存在本地路径，可在提交前统一整理为相对路径或示例路径。
3. 不提交包含账号、令牌、私有地址、真实姓名或联系方式的文件。
4. 不提交大模型权重、数据集和第三方依赖包。
5. 如需展示运行环境，可在说明书中描述 Python、浏览器和依赖库，不需要提交本地环境目录。

## 6. 推荐源代码组织方式

建议将源程序节选按以下顺序组织：

1. 软件入口与 DAG 调度代码；
2. 数据检查和特征提取代码；
3. 工程化报告生成代码；
4. Disease Memory Bank 代码；
5. Association 规则评分代码；
6. 路线级时空 track、比较证据和声明门控代码；
7. 真实巡检输入适配、固定任务契约、状态与受控工作流代码；
8. 规则面积变化提示和复检清单代码；
9. 视频 demo 生成、视频标注和视频产物校验代码；
10. Web 服务和接口代码；
11. 最终报告生成代码。

## 7. 当前版本边界说明

当前版本主要基于 KICT 静态图像 / mask 与仿真巡检元数据进行工程原型验证。视频 demo 由 KICT 静态图像 / mask 合成，用于展示视频输入处理和可视化流程。真实巡检输入适配目前只覆盖单 sequence、已有 mask 的离线数据合同与就绪性校验；受控工作流目前只在隔离临时沙箱中按固定任务契约运行。源代码提交材料应体现软件功能实现，不应把 demo 数据、合成视频、仿真元数据、单轮表观变化或运行产物描述为真实线路长期巡检验证结果。
