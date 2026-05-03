# TunnelDefectSeg v6 — Round 3:评估口径修复

> Round 1 修 bug,Round 2 涨点,Round 3 解决一个被忽视的问题:
> **评估方式可能本身就不对**,在低估真实精度。

---

## 为什么评估口径可能本身有问题

用户反复优化"裂缝 IoU 偏低",但 v5/v6 的评估流程是:

```
原始图像 (任意尺寸, 比如 2048×1024)
    ↓ resize 到 384×384  (BILINEAR)
模型推理
    ↓ 384×384 logits → argmax
    ↓ 与 384×384 GT 比较 (GT 也是从原始 mask NEAREST resize 过来的)
计算 mIoU
```

**两处隐藏损失**:

1. **GT 自己就在掉信息**。1–3 px 的细裂缝在 NEAREST resize 到 384 后,大量直接消失。模型即使预测正确,GT 里都没那条裂缝可对——分母分子双双归零。
2. **IoU 对像素位置极度敏感**。哪怕预测出来的裂缝完全正确、只是沿垂直方向偏 1 px,IoU 就从 1.0 直接掉到 0.5。这跟"模型不行"无关,是 IoU 对线状目标本来就太严苛。

**一个具体的对比**(用合成数据实测):

| 场景 | plain IoU | Tolerance IoU(±2 px) | Boundary F1 |
|---|---|---|---|
| 预测完全正确 | 1.000 | 1.000 | 1.000 |
| 预测沿裂缝偏 1 px | **0.500** | 1.000 | 1.000 |
| 预测沿裂缝偏 5 px | 0.000 | 0.000 | 0.000 |

中间一行就是"用户的 IoU 莫名其妙偏低"的原因。模型完全可能已经在做对的事,只是 IoU 没法度量这件事做得多好。

---

## 这一轮做了什么

### 1. `util/metrics.py`(新文件)

提供完整的指标合集,**与原 `engine.compute_metrics` 完全兼容**(老 import 不动):

| 指标 | 适用 | 说明 |
|---|---|---|
| `compute_metrics` | 通用 | 标准 IoU / Dice / pixel_acc(原版口径) |
| `compute_tolerance_iou` | **裂缝/线状** | GT 与预测各膨胀 ±k px 后再算 IoU。CrackTree200 / Crack500 标准做法 |
| `compute_boundary_f1` | **裂缝/线状** | Csurka 2013 边界 F1,容差 θ px。比 IoU 更敏感地度量"边界对齐"质量 |
| `compute_per_image` | 诊断 | 单张图全套指标,用于 CSV / 失败 case 排查 |
| `StreamingIoU` | 大数据集 | 流式累加 confusion matrix,2048×2048 全分辨率评估也不爆内存 |
| `format_metrics_table` | 日志 | 友好格式化输出 |

**关键不变量**(已用合成数据验证):
- 完美预测 → 所有指标 = 1.0
- 1 px 偏移 → IoU 0.50,Tolerance IoU 1.00,Boundary F1 1.00(成功救场)
- 5 px 偏移 → 三者都失效(防止过度宽容)
- StreamingIoU 与 concat-based 等价(误差 1e-9)

### 2. `test.py`(完全重写)

旧版 80 行,新版做了三件事:

**(a) 全分辨率滑窗推理**:
- 直接遍历 dataset 的 samples,加载原始尺寸图像 + 原始尺寸 GT
- 用 `inference.sliding_window_inference` 在原图分辨率上前向
- 这一项独立就能让裂缝评估重新可信

**(b) 全套指标**:
- mIoU / mDice / pixel_acc / 每类 IoU
- crack_tolerance_iou(±2 px,可调)
- crack_boundary_f1(θ=2,可调)
- crack 像素级 P / R / F1

**(c) 失败 case 诊断**:
- 每张图的指标写入 `per_image_metrics.csv`
- `--save_worst_n 20` 自动把最差 20 张保存为三联图(input | GT | pred)
  并在文件名里写好 mIoU,方便直接打开排查

**用法**:
```bash
# 标准评估
python test.py --weights best.pth --data_path /data

# + TTA + 多尺度 (慢但最准)
python test.py --weights best.pth --data_path /data \
               --tta --multi_scale 0.75 1.0 1.25

# + 保存最差 30 张失败 case
python test.py --weights best.pth --data_path /data --save_worst_n 30

# 兼容老流程: 用 384 resize 评估 (用于和新流程对比)
python test.py --weights best.pth --legacy_resize_eval
```

建议**先用同一个 checkpoint 跑两次**:
```bash
python test.py --weights best.pth --legacy_resize_eval   # 老口径
python test.py --weights best.pth                        # 新口径
```
看两边数字差多少。我赌新口径的 crack IoU 至少高 5–15 个点,且
crack_tolerance_iou 比 crack IoU 又高 5–10 个点。

### 3. `inference.py` 加 `--multi_scale`

```bash
# 单尺度 + 滑窗(round 2)
python inference.py --weights best.pth --image x.jpg --output r.png

# + 多尺度滑窗(新)
python inference.py --weights best.pth --image x.jpg --output r.png \
                    --multi_scale 0.75 1.0 1.25 --tta
```

多尺度对裂缝额外有用:小尺度让模型看到更长距离的连通性,大尺度
让裂缝边界更精细。`(0.75, 1.0, 1.25)` 是 PSPNet/DeepLab 论文常用配置。

### 4. `util/engine.py` 加 `val_one_epoch_rich`

训练时主循环不变(普通 val 还是只算 mIoU,快)。但用户可以**在某些
关键 epoch** 切到 rich val 看裂缝专项指标趋势:

```python
# train.py 主循环里
if epoch % 20 == 0:
    val_one_epoch_rich(val_loader, model, criterion, epoch, logger, config)
else:
    val_one_epoch(val_loader, model, criterion, epoch, logger, config)
```

代价:每张 val 图多算 ~10–30 ms 的距离场,~200 张 val 集多花 1–2 s。
完全可以忽略。

### 5. `tools/smoke_test.py` 扩展

新增 6 项检查,涵盖:
- 1 px 偏移时 Tolerance IoU 真的能拯救
- 完美预测时三个指标都正确给 1.0
- StreamingIoU 与传统 concat-based 数值完全等价(1e-9)
- test.py 可正常 import
- inference.py 支持 `--multi_scale`
- engine.py 暴露 `val_one_epoch_rich`,且老的 `test_one_epoch` 没被破坏

---

## 一个建议的实操流程

```bash
# 0) smoke test, 确认环境干净
python tools/smoke_test.py

# 1) 训练 (现在主循环也可以可选加 rich val, 但默认不开)
python train.py --data_path /data

# 2) 用同一 checkpoint 跑两套评估对比
python test.py --weights best.pth --data_path /data --legacy_resize_eval \
               --out_dir results/legacy_eval/
python test.py --weights best.pth --data_path /data --tta \
               --out_dir results/full_res_eval/ --save_worst_n 30

# 3) 看两个结果的差异
#    - mIoU 应该新口径 > 老口径 (约 +3-8 点)
#    - crack_tol_iou (新指标) 还会再高 5-10 点
#    - results/full_res_eval/outputs/worst_cases/ 里是真正的 hard case

# 4) 看 hard case, 思考是数据标注问题, 模型问题, 还是评估问题
#    - 标注模糊/错的 → 修标注比改算法收益高
#    - 模型确实预测不到 → 加数据增强 / 改架构
#    - 模型预测了但偏了 → 已经被 Tolerance IoU 救了, 不用改

# 5) per_image_metrics.csv 可以拉到 Excel/pandas 做更深的分析,
#    比如 "在 leakageW 这一类上 crack 列同时变差吗" 之类
```

---

## 还能继续做的

只剩下一些"边际"工作了:
- **Boundary IoU**(Cheng 2021,与 Boundary F1 不同):用边界距离场加权 IoU,更细粒度
- **MS Hausdorff distance**:量化边界最坏情况的偏离
- **HD95**(Hausdorff 95th percentile):医学分割常用,对噪声更稳

但说真的——先把现有这套跑一遍,看新口径下数字是什么样。如果裂缝
crack_tol_iou 已经 0.65+,模型其实没问题,问题在原版评估口径 +
原始 mask 的标注精度。如果还是 < 0.5,再考虑模型层面的事。
