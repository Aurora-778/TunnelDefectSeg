# 软著材料整理清单

## 建议软件名称

`隧道病害智能分割与可信复核分析系统 V1.0`

## 申请材料草稿清单

- 软件著作权登记申请表：按申请系统填写，软件名称和版本号需与说明书、源代码页眉一致。
- 软件说明书：可基于 `docs/software-copyright/tunnel-defect-review-system.md` 整理成 PDF。
- 源代码鉴别材料：建议从本仓库自研代码中选取，避免把第三方库源码、模型权重、数据集图片作为源码页。
- 身份证明或单位证明：按申请主体准备。
- 权属说明：如多人共同开发或依托学校项目，应确认署名和权属安排。

## 源代码页建议范围

优先选择本仓库自研、能体现软件功能闭环的代码：

- `web_app.py`
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
- `.codegraph/`、`.understand-anything/` 等本地索引文件。

## 说明书应包含的内容

- 软件名称和版本号。
- 软件用途和适用场景。
- 运行环境。
- 功能模块说明。
- 操作流程。
- 主要界面截图。
- 输出结果说明。
- 第三方依赖说明。
- 非结构安全诊断边界说明。

## 仓库提交前检查

- README 中能找到 Web 启动方式。
- Web 页面能打开并展示拖拽检测入口。
- `docs/software-copyright/tunnel-defect-review-system.md` 已更新到当前功能。
- 源代码材料不包含第三方库源码或模型权重。
- `.codegraph/` 未提交。
- 大体积 `experiments/web_live/` 未提交。
