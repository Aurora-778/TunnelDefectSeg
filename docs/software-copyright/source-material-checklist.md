# 软著材料整理清单

## 建议软件名称

`机器人隧道巡检病害时空分析与复检管理系统 V1.0`

## 申请材料草稿清单

- 软件著作权登记申请表：按申请系统填写，软件名称需与说明书封面、源代码封面一致；版本号保留在登记申请表或正文说明中；封面和页眉按规范不写版本号。
- 软件说明书：可基于 `docs/software-copyright/software-functional-specification.md` 和 `docs/software-copyright/user-manual-draft.md` 整理成 PDF。
- 源代码鉴别材料：建议从本仓库自研代码中选取，避免将第三方库源码、模型权重、数据集图片作为源码页。
- 身份证明或单位证明：按申请主体准备。
- 权属说明：当前建议口径为著作权人“西安理工大学”、第一开发者“陈志鹏”；如还有共同开发者或学校署名要求，应在正式提交前补充确认。

## 按申请规范补充检查

根据用户提供的软著申请规范，正式提交前需要额外检查以下事项：

- 中国版权保护中心办理入口：`https://register.ccopyright.com.cn/registration.html#/index`。
- 登记业务：计算机软件著作权相关登记，选择 R11 软件著作权登记申请。
- 申请前需要完成账号注册和实名认证。
- 若按学校流程办理，我校师生注册身份按规范选择“个人”，登记时按系统要求选择“我是代理人”。
- 本材料以 2026 年 7 月 30 日的功能基线更新：著作权人填写“西安理工大学”、第一开发者填写“陈志鹏”、发表状态填写“未发表”、权利取得方式填写“原始取得”（若系统选项显示“原创取得”，按系统选项填写）。如以该基线申报，开发完成日期建议按“2026 年 7 月 30 日”与内部完成记录核验后填写。
- 提交申请后需要处理授权码、签章页打印、学校审批、盖章页上传和授权证书成果登记等流程。
- 学生申请或教师申请的学校端入口可能不同，需由指导老师确认学校端申请类型、授权码、签章页打印和盖章流程。

## 用户使用说明书格式检查

- 封面标题建议为：`机器人隧道巡检病害时空分析与复检管理系统 用户使用说明书`。
- 封面和页眉不写版本号，版本号保留在登记信息或正文说明中。
- 页眉只放软件名称，不放“V1.0”等版本号。
- 页码放在页面右上端，与页眉同一行。
- 正式 Word / PDF 版至少包含一张图片，可使用系统流程图、Web Dashboard 系统总览截图或视频分析结果页面截图。
- 图片编号建议按章编号，例如 `图2.1 系统总览界面`。
- 标题、正文、图名应按规范模板统一字体、字号、段前段后和行距。

## 源代码材料格式检查

- 封面标题建议为：`机器人隧道巡检病害时空分析与复检管理系统 源代码`。
- 封面和页眉不写版本号，页眉只放软件名称。
- 源代码材料不需要目录。
- 若总程序不满 60 页，第一页标题写 `源程序`。
- 若总程序超过 60 页，第一页标题写 `源程序前30页`，第 31 页标题写 `源程序后30页`。
- 超过 60 页时应保证最后一页为满页，避免末页只有少量代码。
- 源代码正文可以在每段代码前标注仓库相对路径，例如 `orchestrator/agents/association_agent.py`。
- 不应出现个人绝对路径、账号、令牌、模型权重、数据集图片、缓存或日志。

## 源代码页建议范围

优先选择本仓库自研、能体现软件功能闭环的代码：

- `run.py`
- `web_app.py`
- `orchestrator/agents/engineering_report_agent.py`
- `orchestrator/agents/memory_agent.py`
- `orchestrator/agents/association_agent.py`
- `orchestrator/agents/visualization_agent.py`
- `orchestrator/agents/final_report_agent.py`
- `scripts/extract_kict_mask_features.py`
- `scripts/merge_kict_with_simulation.py`
- `scripts/generate_engineering_report.py`
- `scripts/analyze_disease_growth.py`
- `scripts/generate_visualization_and_recheck_list.py`
- `scripts/create_demo_tunnel_video_from_kict.py`
- `scripts/run_video_inspection_pipeline.py`
- `scripts/annotate_video_frames.py`
- `scripts/export_annotated_video.py`
- `scripts/validate_video_artifacts.py`
- `scripts/run_demo_showcase.py`
- `robot_sequence.py`
- `spatiotemporal_monitoring.py`
- `robot_inspection_report.py`
- `scripts/prepare_real_inspection_pilot.py`
- `scripts/run_inspection_workflow.py`
- `orchestrator/inspection_workflow/contracts.py`
- `orchestrator/inspection_workflow/controller.py`
- `orchestrator/inspection_workflow/lifecycle.py`
- `orchestrator/state/store.py`

以下早期单图检测相关文件可作为补充材料，但不建议覆盖主线；`web_app.py` 已在主线代码列表中体现，此处不重复列出：

- `web_demo/index.html`
- `run_confidence_risk.py`
- `adaptive_fusion.py`
- `morphology_adapter.py`
- `risk_adapter.py`
- `enhancement_evidence.py`
- `evaluate_confidence_risk.py`
- `data_adapter.py`
- `metrics_adapter.py`
- `segformer_tools.py`

不建议作为源代码页主体：

- `third_party/` 下第三方源码。
- `experiments/` 下大体积运行产物。
- 模型权重 `.pth`。
- 数据集图片和人工 mask。
- 生成的视频、抽帧图片、视频巡检 CSV 和标注视频产物，例如 `data/videos/`、`data/video_frames/`、`data/video_inspection/`、`data/video_masks/`、`outputs/video_inspection/`。
- 某次受控工作流的 `runs/`、`logs/`、状态快照、操作日志、恢复标志和发布清单；这些是运行证据，不是源程序。
- `.codegraph/`、`.understand-anything/` 等本地索引文件。

## 说明书应包含的内容

- 软件名称和版本号。
- 软件用途和适用场景。
- 运行环境。
- 功能模块说明。
- 操作流程。
- 主要界面截图。
- 视频分析结果页面或系统总览界面截图。
- 路线级报告或复检队列截图，并在图注中保留“趋势表述受比较条件和人工复核约束”的说明。
- 输出结果说明。
- 第三方依赖说明。
- 非结构安全诊断边界说明。

## 仓库提交前检查

- README 中能找到 Web 启动方式。
- Web 页面能打开并展示系统总览、视频分析结果或拖拽检测入口。
- `run_demo_showcase.bat` 可作为本地视频 demo 展示入口；若未安装 Supervision，可使用 `--skip_supervision`。
- `scripts/prepare_real_inspection_pilot.py --validate-only` 可检查单 sequence、已有 mask 的真实巡检输入数据合同；它不等同于在线推理或真实跨轮评测。
- `scripts/run_inspection_workflow.py --plan-only` 可检查受控工作流计划；该工作流只能在隔离临时沙箱中执行，不通过 Web 页面启动。
- `docs/software-copyright/tunnel-defect-review-system.md` 已标记为早期单图分割与可信复核能力的历史补充参考，不作为本次主提交材料。
- 源代码材料不包含第三方库源码或模型权重。
- 源代码材料不包含本地生成的视频 demo 产物。
- `.codegraph/` 未提交。
- 大体积 `experiments/web_live/` 未提交。
