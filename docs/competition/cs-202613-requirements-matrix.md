# CS-202613 Requirements Matrix

这份矩阵把比赛要求拆成“当前状态、仓库证据、下一步动作”，方便老师快速判断哪些已经能展示，哪些还是计划中的扩展。

## Status Legend

- `supported`: 当前仓库里已有对应能力或可直接引用的证据。
- `planned`: 目标明确，但主分支里还没有完整落地。
- `simulation-only`: 只能用仿真、标定或估算方式展示，不能冒充外业实测。
- `out-of-scope`: 当前作品身份不主张。

## Matrix

| Req ID | Requirement | Current status | Repo evidence | Gap / next step |
|---|---|---|---|---|
| R1 | 土建结构、轨道系统、系统设备三大领域统一组织输出 | planned | `docs/plans/2026-06-18-001-feat-metro-multidomain-inspection-plan.md` | 需要统一多领域 schema 和 Web 分组浏览入口。 |
| R2 | 土建结构病害可复用现有 SegFormer 6 类分割能力 | supported | `README.md`; `docs/competition/experiment-summary.md`; `tests/test_segformer_inference_adapter.py` | 下一步是把 6 类结果映射到比赛更通俗的病害表达。 |
| R3 | 轨道和设备病害要有统一检测结果结构 | planned | `docs/plans/2026-06-18-001-feat-metro-multidomain-inspection-plan.md` | 先定义 `domain / defect_type / geometry / confidence / review_priority`，再接示例 detector。 |
| R4 | 每个结果都要包含类型、置信度、几何和空间位置等信息 | supported | `inspection_report.py`; `robot_inspection_report.py`; `docs/competition/robot-spatiotemporal-monitoring.md` | 单图报告和路线级报告已覆盖核心字段，真实外业传感器字段仍按 prototype 边界说明。 |
| R5 | 像素坐标要能映射到里程、环号、时钟方位和局部三维坐标 | supported | `spatial_mapping.py`; `tests/test_spatial_mapping.py`; `tests/test_run_confidence_risk_spatial_mapping.py` | 当前已具备图像中心、钟位、环号/里程和可选局部三维输出，但仍应按 prototype 讲，不宣称外业级精度。 |
| R6 | 空间映射必须区分真实传感器、仿真输入和手动标定输入 | supported | `spatial_mapping.py`; `run_confidence_risk.py`; `multidomain_schema.py`; `web_demo/index.html` | 已有 `location_source` / `accuracy_level` / `status` 合同，但真实传感器与标定输入仍需后续按数据源补充。 |
| R7 | RGB、深度、点云、内参、外参、姿态和里程信息要留扩展接口 | supported | `spatial_mapping.py`; `run_confidence_risk.py` | 当前已保留 metadata / local_3d 接口，但点云与完整姿态融合仍是后续扩展。 |
| R8 | 无点云时允许用隧道圆筒/环片参数做仿真定位 | planned | `spatial_mapping.py`; `docs/competition/spatial-mapping-method.md` | 当前实现先覆盖图像平面 prototype，圆筒/环片先验还需要继续补。 |
| R9 | uncertainty、disagreement、morphology_delta 和 review_priority 要进入报告 | supported | `inspection_report.py`; `docs/competition/report-template.md`; `docs/competition/experiment-summary.md` | 继续保持这些字段和当前 Web/导出合同一致。 |
| R10 | 报告要区分算法结论、复核建议和人工确认状态 | supported | `docs/competition/report-template.md`; `docs/patent-notes/tunnel-defect-confidence-risk.md` | 不能把 review priority 写成结构安全诊断。 |
| R11 | Web 展示要支持按领域、病害类型、位置和复核优先级浏览 | supported | `web_app.py`; `web_demo/index.html`; `web_demo/assets/robot_route_report.json` | 已有多领域面板和机器人路线报告样例，后续可继续做交互式筛选和多期时间线。 |
| R12 | 技术报告要覆盖架构、算法、融合/定位、实验和局限性 | planned | `docs/plans/2026-06-18-001-feat-metro-multidomain-inspection-plan.md` | 还需要落成 `technical-report-outline.md` 之类的正式大纲。 |
| R13 | 主 README 要面向开源和评委展示，避免本机绝对路径 | supported | `README.md`; `tests/test_docs_artifact_contract.py` | 保持主 README 只写通用入口，本机细节放到 local setup。 |
| R14 | 作品包要包含代码说明、运行入口、示例数据、实验结果和材料索引 | planned | `docs/competition/experiment-summary.md`; `docs/competition/demo-case-pack.md`; `docs/competition/report-template.md` | 还需要把技术报告、PPT、演示脚本和提交清单补齐成完整打包目录。 |
| R15 | 所有指标必须能追溯到日志、JSON、测试或比赛样本评估 | supported | `tests/test_docs_artifact_contract.py`; `docs/competition/experiment-summary.md`; `docs/paper/claim-to-evidence-matrix.md` | 保持所有展示表格继续标注 internal dataset、simulation 或 pending。 |

## Presentation-safe Summary

- 当前已经能稳定展示的是土建 SegFormer 结果、可信复核证据、报告导出和 Web 演示。
- 比赛主线里的多领域适配、空间定位和完整作品包，还在按计划补齐。
- 没有官方样本或真实传感器数据时，任何定位或评分都必须明确标注为 simulation-only、calibration prototype 或 planned。

## What Not To Claim Yet

- 不要把模拟定位写成外业精确定位。
- 不要把轨道/设备示例适配写成已完成的完整训练模型。
- 不要把当前 Web 演示直接包装成比赛全部能力的最终版。
