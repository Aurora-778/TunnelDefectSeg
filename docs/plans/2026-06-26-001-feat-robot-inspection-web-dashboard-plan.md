---
title: Robot Inspection Web Dashboard Plan
type: feat
status: completed
date: 2026-06-26
origin: docs/plans/2026-06-25-001-feat-robot-spatiotemporal-defect-monitoring-plan.md
---

# Robot Inspection Web Dashboard Plan

## Summary

把现有 Web 展示从“单张图片病害分割 Demo”调整为“机器人巡检结果 Dashboard”。首页以巡检总览、里程风险、重点复检清单和病害证据为主，单图拖拽检测保留为现场复核入口；界面说明文字只保留必要标签和简短提示。

---

## Problem Frame

项目目标已经从单图模型展示转向机器人巡检。当前 Web 已能展示 mask、uncertainty、morphology、adaptive selection，也有 `/api/robot-route-report` 和 `web_demo/assets/robot_route_report.json`，但页面叙事仍偏“模型样例展示”和“方法说明”。老师或评委打开页面时，更需要先看到机器人巡检完成后系统给出的工程结论：巡检范围、病害数量、重点复检对象、里程段风险、增长趋势和证据图。

本计划只调整 Web 展示层和轻量数据入口，不训练模型、不下载数据、不改变 KICT 原始数据，也不把规则化趋势证据包装成现场验证预测。

---

## Requirements

**Robot dashboard first**

- R1. 首页首屏必须展示机器人巡检总览，而不是大幅模型宣传 hero。
- R2. 首页必须显示巡检批次、帧数或样本数、病害对象数、重点复检数、高风险数、增长趋势分布等核心指标。
- R3. 页面必须展示里程段风险分布，优先复用 `outputs/visualizations/mileage_risk_distribution.png` 或结构化统计数据。
- R4. 页面必须展示重点复检清单，至少包含病害编号、类型、位置、方位、趋势、风险、复检原因和复检建议。

**Evidence and drilldown**

- R5. 用户点击复检清单中的病害后，应能看到该病害的工程化描述、增长描述、关键指标和可用证据图。
- R6. 原有单图拖拽检测必须保留，但降级为“单图检测 / 现场复核”区域，不再作为首页主叙事。
- R7. 页面必须继续支持已有 mask 视图：原图、GT 标注、单次 mask、融合 mask、选择 mask、叠加图、不确定性图、分歧图、骨架图。
- R8. 页面必须明确区分 `mIoU`、`Self IoU`、规则趋势证据、工程位置和人工复核建议，避免把无 GT 自选图片讲成真实精度验证。

**Minimal explanation**

- R9. 界面说明文字要短，只保留必要标签、状态、单位和一行以内提示；不放大段项目介绍。
- R10. 专业名词可以保留英文，例如 `mIoU`、`Self IoU`、`mask`、`GT`、`uncertainty`、`dashboard`。
- R11. 所有趋势和位置结论必须显示 claim boundary，例如 `rule evidence`、`requires review`、`simulation/coarse` 或 limitation。

---

## Key Technical Decisions

- **KTD1. Dashboard is the default landing state:** 首屏直接呈现巡检结果，不再使用营销式 hero 或大量方法说明，因为当前目标是工程演示而不是模型宣传页。
- **KTD2. Reuse generated artifacts first:** 第一版优先消费 `web_demo/assets/robot_route_report.json`、`data/simulated/priority_recheck_list.csv`、`data/simulated/disease_growth_analysis.csv` 和 `outputs/visualizations/*.png`，避免新增训练或推理依赖。
- **KTD3. Add a small dashboard payload rather than parsing CSV in the browser:** 后端 `web_app.py` 负责把 CSV/JSON 统一成 `/api/robot-dashboard` 响应，前端只渲染结构化数据，降低页面脚本复杂度。
- **KTD4. Keep single-image detection as a secondary workflow:** 拖图实时检测仍是项目能力的一部分，但它服务于现场复核和证据补充，不再主导首页。
- **KTD5. Claim boundary is data, not prose:** dashboard payload 应携带 `claim_level`、`measurement_basis`、`comparability_status`、`limitations` 或等价字段；前端用短标签显示，不靠长文解释风险。

---

## High-Level Technical Design

```mermaid
flowchart TB
  A[Generated robot artifacts] --> B[web_app.py dashboard loader]
  A1[robot_route_report.json] --> B
  A2[priority_recheck_list.csv] --> B
  A3[disease_growth_analysis.csv] --> B
  A4[visualization PNG outputs] --> B
  B --> C[/api/robot-dashboard]
  C --> D[web_demo/index.html]
  D --> E[Inspection overview]
  D --> F[Recheck list]
  D --> G[Mileage risk and charts]
  D --> H[Defect evidence detail]
  D --> I[Single-image review tab]
```

---

## Implementation Units

### U1. Dashboard data payload

- **Goal:** 新增一个稳定的 robot dashboard 数据入口，把机器人巡检报告、增长分析和复检清单整理成前端可直接渲染的 JSON。
- **Files:** `web_app.py`, `tests/test_web_app.py`
- **Patterns:** 参考 `_load_robot_route_report()` 的静态文件加载和 fallback 风格；CSV 读取使用标准库 `csv`，保持轻量。
- **Expected payload:** `summary`、`route_report`、`priority_rechecks`、`growth_distribution`、`attention_distribution`、`visualization_links`、`limitations`。
- **Test scenarios:**
  - 存在 `priority_recheck_list.csv` 和 `disease_growth_analysis.csv` 时，payload 返回复检清单和统计分布。
  - 缺少复检 CSV 时，payload 不崩溃，并在 `limitations` 中说明数据缺失。
  - `/api/robot-dashboard` 返回 `ok: true`，且不破坏 `/api/robot-route-report`。
  - visualization links 使用 `/outputs/visualizations/...`，浏览器可通过已有 `/outputs/` 静态通道访问。
- **Verification:** `python -m pytest --rootdir . tests/test_web_app.py -q -p no:cacheprovider`

### U2. Minimal robot inspection landing view

- **Goal:** 重构 `web_demo/index.html` 的首屏，让 Dashboard 成为默认入口，减少宣传型说明文字。
- **Files:** `web_demo/index.html`, `tests/test_web_app.py`
- **Patterns:** 复用现有单页和静态资产结构；不引入前端框架；避免嵌套卡片和大段解释。
- **UI structure:**
  - 顶部状态栏：巡检批次、样本/帧数、病害对象数、重点复检数、高风险数。
  - 主区域：左侧里程风险图或路线摘要，右侧重点复检清单。
  - 次级区域：增长趋势分布、关注等级分布、病害类型分布。
  - 标签切换：`巡检总览`、`重点复检`、`病害证据`、`单图检测`。
- **Test scenarios:**
  - HTML 中存在机器人巡检 Dashboard 入口和 `api/robot-dashboard` 请求。
  - 页面保留单图检测上传入口。
  - 页面保留必要专业词：`mIoU`、`Self IoU`、`mask`、`GT`、`uncertainty`。
  - 页面不再把长篇方法介绍放在首屏。
- **Verification:** `node --check` 检查页面脚本；`python -m pytest --rootdir . tests/test_web_app.py -q -p no:cacheprovider`

### U3. Recheck list and defect evidence drilldown

- **Goal:** 让复检清单成为机器人巡检闭环的核心操作区，点击病害可查看位置、趋势、复检原因和图像证据。
- **Files:** `web_demo/index.html`, `web_app.py`, `tests/test_web_app.py`
- **Patterns:** 复用现有 image view gallery 和 `outputs/` 静态服务；不新增复杂路由。
- **Display fields:** `priority_rank`、`disease_id`、`disease_type`、`attention_level`、`growth_trend`、`area_growth_rate`、`last_risk_level`、`last_mileage_range`、`main_clock_direction`、`recheck_reason`、`recheck_suggestion`、`growth_description`。
- **Test scenarios:**
  - 复检清单按 `priority_rank` 显示。
  - 点击或选择病害后，详情区显示增长描述和复检建议。
  - 没有复检对象时，页面显示简短空状态，不显示错误堆栈。
  - 详情区显示 claim boundary 或 limitations 标签。
- **Verification:** `python -m pytest --rootdir . tests/test_web_app.py -q -p no:cacheprovider`

### U4. Preserve single-image detection as review mode

- **Goal:** 保留拖图实时检测能力，但把它放到“单图检测 / 现场复核”区域。
- **Files:** `web_demo/index.html`, `web_app.py`, `tests/test_web_app.py`
- **Patterns:** 不改 `_detect_image()` 的模型推理行为；只调整前端布局和文字层级。
- **Test scenarios:**
  - 上传图片后仍能调用 `/api/detect` 并渲染原有 views。
  - 自选图片无 GT 时仍显示 `mIoU=N/A` 和 `Self IoU`，不误导为真实精度。
  - uncertainty unavailable 时显示短提示，不占用大段说明。
  - 原图、mask、overlay、uncertainty、disagreement、skeleton 按现有 view keys 可切换。
- **Verification:** `python -m pytest --rootdir . tests/test_web_app.py -q -p no:cacheprovider`

### U5. Visual polish and browser verification

- **Goal:** 让页面在老师展示时看起来像一个巡检系统，而不是论文实验页；同时保证桌面和窄屏不重叠。
- **Files:** `web_demo/index.html`, `tests/test_docs_artifact_contract.py`
- **Patterns:** 使用紧凑 dashboard 风格；保留项目现有暗色工业风，但减少装饰性 hero、长段说明和重复卡片。
- **Test scenarios:**
  - 桌面宽屏下首屏能看到总览指标和复检清单。
  - 窄屏下列表、图表和按钮不重叠。
  - PNG 图表能正常显示，路径来自 `/outputs/visualizations/`。
  - PPT 或 docs artifact contract 如依赖 Web demo 资产，需要同步更新测试断言。
- **Verification:** `node --check`；必要时用浏览器人工检查 `http://127.0.0.1:8000/`。

---

## Scope Boundaries

**In scope**

- Web 展示信息架构调整。
- 机器人 dashboard 数据入口。
- 重点复检清单展示。
- 里程风险和增长可视化展示。
- 单图检测保留为复核模式。

**Out of scope**

- 训练或更换模型。
- 下载新数据。
- 修改 KICT 原始数据。
- 接入真实机器人 middleware。
- 新增 SLAM、LiDAR 或三维重建。
- 把规则化趋势证据写成真实现场预测。

---

## Acceptance Examples

- AE1. 打开首页时，用户首先看到巡检批次、病害对象数、重点复检数和里程风险图，而不是大幅单图模型 hero。
- AE2. 点击 `D001` 等复检对象时，页面显示位置、方位、风险、增长趋势、复检原因和增长描述。
- AE3. 切到 `单图检测` 后，用户仍能拖入图片实时检测，并看到原有 mask / overlay / uncertainty / skeleton 视图。
- AE4. 对无 GT 自选图片，页面显示 `mIoU=N/A` 和 `Self IoU`，不把模型间一致性说成真实精度。
- AE5. 缺少 dashboard CSV 或图表时，页面显示简短缺失状态，后端 API 不返回 500。

---

## Risks & Dependencies

| Risk | Impact | Mitigation |
| --- | --- | --- |
| 页面解释过多 | 老师看不出系统主线 | UI 文案限制为短标签和一行提示，详细解释放报告/PPT |
| 数据来源分散 | 前端脚本复杂、容易路径错 | 后端聚合 `/api/robot-dashboard`，前端只渲染 payload |
| 复检清单全是重点关注 | 展示层看起来缺少区分度 | 显示排序依据、风险和趋势字段，后续算法可继续细化 |
| 图表 PNG 不存在 | Dashboard 缺关键视觉证据 | API 返回 visualization limitations，页面给短空状态 |
| 误导性结论 | 被质疑夸大预测能力 | 所有趋势和位置结论保留 claim boundary / limitations |

---

## Documentation / Operational Notes

- README 暂不需要大改，只需在后续实现完成后补充 Web 展示入口说明。
- 如果 Web 端截图或 PPT 页面引用旧首页，需要在实现后同步更新。
- 当前本地 Web 可能已运行在 `http://127.0.0.1:8000/`；实现阶段应先复用现有启动脚本和端口检查。

---

## Sources / Research

- `web_app.py` 已提供 `/api/robot-route-report`、`/api/evidence`、`/api/detect` 和 `/outputs/` 静态服务入口。
- `web_demo/index.html` 是当前主展示页，包含单图检测、指标卡、morphology、multidomain 等前端逻辑。
- `web_demo/assets/robot_route_report.json` 是现有 robot route 静态展示资产。
- `data/simulated/priority_recheck_list.csv`、`data/simulated/disease_growth_analysis.csv` 和 `outputs/visualizations/*.png` 是新 dashboard 可直接消费的数据与图表。
- `tests/test_web_app.py` 已覆盖 robot route report loader 和 detect image 行为，是实现阶段的主要回归测试入口。
