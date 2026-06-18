# 隧道病害分割与可信复核分析系统

基于 SegFormer 的隧道病害语义分割，结合 confidence-risk 增强模块，提供 Web 可视化展示。

## 项目概述

- 训练 6 类隧道病害分割模型
- 使用 SegFormer B1 提升 mask 质量
- 后处理增强模块：TTA 融合、自适应选择、不确定性估计、骨架提取
- Web 端支持拖拽图片实时检测与多视图展示
- 结构化巡检报告导出（`inspection_report.py` / `docs/competition/report-template.md`）
- 输出复核优先级（review priority）和专利证据包

## 类别定义

| ID | Class | 中文说明 |
|---:|---|---|
| 0 | background | 背景 |
| 1 | simple | simple 类病害 |
| 2 | blocky | 块状病害 |
| 3 | pipeline | 管线类病害 |
| 4 | vertical | 竖向病害 |
| 5 | horizontal | 横向病害 |

## 数据与训练约定

- 输入尺寸：`384 × 384`
- 数据划分：`train / val / test = 700 / 150 / 150`
- 标签模式：`multiclass`
- SegFormer 训练：`160000 iter`
- 评价指标：`mIoU`、`mAcc`、`aAcc`，以及各类别 `IoU / Acc`

## 关键文件

| 文件 | 说明 |
|---|---|
| `data_adapter.py` | 数据发现、划分、读取 |
| `metrics_adapter.py` | 流式混淆矩阵指标（PA、mIoU、mDice） |
| `build_6class_labels.py` | 生成 6 类标签、清单和摘要 |
| `segformer_tools.py` | 生成 SegFormer/mmseg 配置和启动脚本 |
| `run_confidence_risk.py` | 推理后增强模块（mask、uncertainty、skeleton、报告） |
| `run_multidomain_inspection.py` | 轨道/设备 demo 组合报告入口 |
| `evaluate_confidence_risk.py` | 有 GT 标注时的增强模块评估 |
| `enhancement_evidence.py` | 增强证据汇总 |
| `morphology_adapter.py` | 形态量化（面积、连通域、骨架、方向） |
| `adaptive_fusion.py` | 自适应 mask 选择逻辑 |
| `multidomain_detectors.py` | 轨道/设备 demo 适配器和多领域示例结果 |
| `web_app.py` | 本地 Web 检测服务 |
| `web_demo/index.html` | Web 前端展示界面 |

## SegFormer B1 训练结果

| Iter | mIoU | mAcc | aAcc |
|---:|---:|---:|---:|
| 160000 | 84.33% | 91.17% | 98.62% |

各类别结果：

| Class | IoU | Acc |
|---|---:|---:|
| background | 99.01 | 99.51 |
| simple | 80.23 | 88.40 |
| blocky | 53.58 | 75.56 |
| pipeline | 94.16 | 97.43 |
| vertical | 95.85 | 97.75 |
| horizontal | 83.17 | 88.34 |

## 增强模块

`run_confidence_risk.py` 在模型推理之后执行以下处理：

1. 轻量 TTA（原图 + 水平翻转）
2. 概率图对齐与融合
3. 生成 single / fused / hybrid mask
4. 自适应选择 selected_mask（小目标保护、置信度、连通区域）
5. 计算 uncertainty、disagreement、骨架、方向、面积和风险解释

### 使用示例

```bash
# 单张图或文件夹推理
python run_confidence_risk.py <image-or-folder> --output-dir experiments/confidence_risk --tta-mode light

# 使用 SegFormer checkpoint 推理
python run_confidence_risk.py <image-or-folder> \
  --model-source segformer \
  --segformer-config experiments/segformer_b1/configs/segformer_b1_6cls.py \
  --segformer-checkpoint experiments/segformer_b1/runs/segformer_b1_6cls/latest.pth \
  --output-dir experiments/segformer_b1/confidence_risk
```

### 输出产物

- `*_single_mask.png` / `*_fused_mask.png` / `*_hybrid_mask.png`
- `*_selected_mask.png`
- `*_overlay.png` / `*_selected_overlay.png`
- `*_uncertainty_heatmap.png` / `*_disagreement_heatmap.png`
- `*_skeleton.png`
- `*_report.json`

## 增强模块评估

在有 GT 标注的数据上评估增强效果：

```bash
# test split 评估
python evaluate_confidence_risk.py --split test --limit 20 --output experiments/confidence_risk_eval.json

# val split 上搜索自适应阈值
python evaluate_confidence_risk.py --split val --limit 0 --search-config --output experiments/adaptive_fusion_config_search_val.json
```

### 当前评估结果

| Split | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fused |
|---|---:|---:|---:|---:|---:|
| `val` | 150 | 0.3238 | 0.3091 | 0.3261 | +0.0170 |
| `test` | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 |
| `all` | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 |

不确定性到错误区域证据：

| Split | Protected pixels | Successful guard | Error coverage | HU error precision |
|---|---:|---:|---:|---:|
| `test` | 156,301 | 72/150 | 45.66% | 80.66% |
| `all` | 1,230,616 | 614/1000 | 44.54% | 78.27% |

## 复核优先级（Review Priority）

增强模块在 risk 之外新增 `review_priority`——人工复核排序信号，综合以下因素：

- 病害风险（类别、面积、连通域、骨架、uncertainty）
- single/fused 自一致性
- fixed fusion 收缩程度
- selected-mask 选择理由

有 GT 的样本还会输出 `uncertainty_calibration` 校准证据。

> `review_priority` 表示"建议人工优先复核"，不是结构安全诊断或最终养护决策。

## Web 实时展示

启动本地 Web 服务：

```bash
python web_app.py --host 127.0.0.1 --port 8000 --model-source segformer
```

打开 `http://127.0.0.1:8000`，支持拖拽上传图片，实时展示：

- 原图 / GT 标注（数据集样本）/ 预测 mask
- 单次 / 融合 / 选择 mask 及叠加图
- 不确定性图 / 分歧图 / 骨架图
- JSON 风险报告 / 复核优先级

健康检查：`http://127.0.0.1:8000/api/health`

## 比赛、专利与软著材料

- 比赛实验总览：`docs/competition/experiment-summary.md`
- 比赛案例包：`docs/competition/demo-case-pack.md`
- 比赛要求矩阵：`docs/competition/cs-202613-requirements-matrix.md`
- 土建病害映射说明：`docs/competition/civil-defect-evaluation.md`
- 轨道/设备适配说明：`docs/competition/multidomain-detector-notes.md`
- 报告模板：`docs/competition/report-template.md`
- 技术报告大纲：`docs/competition/technical-report-outline.md`
- 演示脚本：`docs/competition/demo-script.md`
- 专利交底书与权利要求：`docs/patent-notes/`
- 消融实验矩阵：`docs/experiments/patent-ablation-summary.md`
- 代表案例清单：`docs/experiments/patent-case-pack.md`
- 论文大纲与证据矩阵：`docs/paper/`
- 软著说明书草稿：`docs/software-copyright/`

## 环境要求

核心依赖版本：

- `torch == 1.10.0`
- `torchvision == 0.11.1`
- CUDA runtime `11.3`
- `mmcv-full == 1.4.0`
- `mmsegmentation == 0.11.0`
- Windows 本地训练/推理细节见 `docs/local-setup-windows.md`

## 注意事项

- mmseg 配置中的路径使用正斜杠（Windows 兼容）
- 单卡训练使用 `BN`，不使用 `SyncBN`
- 默认使用 `--model-source segformer`
- 兼容旧基线：`legacy_resnet50_fcn`
- 自选上传图片无 GT mask，true mIoU 显示为 N/A
- `.codegraph/` 为本地代码索引，不应提交

## 文档索引

- `docs/experiments/enhancement-evidence-summary.md`
- `docs/experiments/patent-ablation-summary.md`
- `docs/experiments/patent-case-pack.md`
- `docs/paper/confidence-review-outline.md`
- `docs/paper/claim-to-evidence-matrix.md`
- `docs/software-copyright/tunnel-defect-review-system.md`
- `docs/software-copyright/source-material-checklist.md`
- `not ground-truth mIoU` 只表示自一致指标，不是人工标注准确率
- `mask_source` 会显式记录当前使用的是 `segformer_b1` 还是 `legacy_resnet50_fcn`

## 目录结构

```text
data_adapter.py          # 数据适配
metrics_adapter.py       # 指标计算
morphology_adapter.py    # 形态量化
adaptive_fusion.py       # 自适应融合
enhancement_evidence.py  # 增强证据汇总
run_confidence_risk.py   # 增强模块推理入口
evaluate_confidence_risk.py  # 增强模块评估
web_app.py               # Web 服务入口
docs/                    # 文档（方案、实验、论文、专利、软著）
experiments/             # 实验产物
web_demo/                # Web 前端
configs/                 # 模型配置
tests/                   # 测试
```
