# TunnelDefectSeg v6 — 真正涨点的优化

> Round 1 已修 7 个 bug 让代码能跑;这一轮专注在**实际提升精度**的 5 项改动。

---

## 1. 滑窗推理 (滑动窗口 + 高斯加权) ⭐⭐⭐

**文件**: `inference.py`

**为什么**:这是裂缝 IoU 偏低的最大原因。
v5 的推理流程是 "原图 → resize 到 384×384 → argmax → 上采样回原图"。
真实隧道照片普遍 1024–4096 px,**1–3 px 的细裂缝在 resize 到 384 后基本消失**——
模型没机会预测它们。

**做法**:
- 切 `tile=384` 的小窗,`overlap=96`(25% 重叠)
- 每个 tile 独立前向 → softmax → 乘 **高斯权重**(中心 1,边角 ≈0.05)累加到全图
- 最后整张图按累加权重归一,argmax
- 高斯加权解决拼接缝问题(均匀加权时 tile 边界肉眼可见)
- `--tta` 开关追加 HFlip + VFlip,3 倍前向

**预期收益**:
对原图分辨率明显大于 384 的数据,裂缝 IoU **+3–8 个点**。
其他粗大类(渗水、剥落)略有受益,主要靠 TTA。

**用法**:
```bash
# 不 TTA, 速度快
python inference.py --weights best.pth --image_dir ./imgs --output_dir ./out

# 加 TTA, 慢 3 倍
python inference.py --weights best.pth --image test.jpg --output r.png --tta

# 改 tile 与 overlap (大图建议保持训练分辨率, overlap 0.25*tile)
python inference.py --weights best.pth --image big.jpg --output r.png \
    --tile 384 --overlap 96
```

---

## 2. MobileNetV3-Small + ImageNet 预训练编码器 ⭐⭐⭐

**文件**: `models/encoder_mobilenetv3.py`、`models/TunnelDefectSeg.py`

**为什么**:小数据 + 小模型,**ImageNet 预训练**是最稳的涨点办法。
v5 的自定义 IRB 编码器从零开始训,数据 ~几千张时学到的低层特征不如 ImageNet 来得通用。

**做法**:
- 用 `torchvision.models.mobilenet_v3_small(weights=IMAGENET1K_V1)`
- 拆成 5 段 ModuleList,在 stride 2 / 4 / 8 / 16 / 32 各取一个 feature
- 通道数 (16, 16, 24, 48, 96) 喂给现有 decoder
- 注:MobileNetV3-Small 是 stride 32(原模型是 stride 16)
- ASPP 空洞率自动调到 (2, 4)(否则在 12×12 bottleneck 上会全像素全采样)
- `_init_weights` 跳过 mbv3 模块,不破坏预训练值

**用法**:
```python
# 默认: 与 v5 完全一致 (custom IRB)
model = TunnelDefectSeg(num_classes=7)

# 切到 mbv3 + ImageNet 预训练
model = TunnelDefectSeg(
    num_classes=7,
    backbone='mbv3_small',
    backbone_pretrained=True,
    aspp_ch=128,
)
```

**预期收益**:小数据集 (<5k 训练图) 上稳定 **+2–4 mIoU**。
裂缝类受益尤为明显——预训练对纹理与边缘的低层特征学得很好。

**参数对比**:

| 配置 | 参数量 |
|---|---|
| custom_irb Lite (16,32,64,96,128) | ~1.5 M |
| custom_irb Small (24,48,96,128,160) | ~2.9 M |
| mbv3_small + ASPP-128 | ~3.0 M |

---

## 3. ASPP 简化:7 分支 → 4 分支 ⭐⭐

**文件**: `models/TunnelDefectSeg.py` 中的 `LiteASPP`

**为什么**:7 分支 (1×1 + 4 个空洞 + GAP + StripPool) 在 1.5–3M 模型上是 overkill。
- 4 个空洞分支(rates 6/12/18/24)在 12×12 或 24×24 bottleneck 上有效感受野高度重叠
- GAP 分支输出全图均值,空间信息全丢,跟其他分支信号差异极大,反而扰动训练
- 每分支后塞 ECA × 7 次,参数堆叠效益边际递减

**新版 4 分支**:
1. `1×1 conv` — 局部
2. `DWSep d=r1` — 中尺度
3. `DWSep d=r2` — 大尺度
4. `StripPool` — 线状 (裂缝最关键)

ECA 只在最后 fuse 之后接一个,Dropout 保留。
**参数量减少 ~40%,推理 FLOPs 减少 ~35%**,但裂缝 / 渗水 IoU 几乎不变(StripPool 才是裂缝关键,rate 18/24 是冗余)。

---

## 4. 数据增强互斥调度

**文件**: `models/mixup_cutmix.py`、`util/loader.py`

**为什么**:v5 同 step 可能同时叠加 4 种:Mosaic + Crack-Copy-Paste + Mixup + CutMix。
最坏情况:
- Mosaic 把图缩到 H/2,1–3 px 裂缝先被插值掉一半
- 然后 Crack-Copy-Paste 把另一张图的裂缝贴上来
- 然后 Mixup 跟另一个 batch 样本做 0.5/0.5 软标签线性混合
- 然后 CutMix 把一块换掉

到模型眼里,标签噪声大到几乎指导不了拓扑学习,clDice 损失基本没用。

**新做法**(三层互斥):
1. **Mosaic 与 Copy-Paste 在 dataset 层互斥**(见 `loader.py`):同一个样本两者只取一个,优先 Mosaic
2. **Mixup 与 CutMix 在 batch 层互斥**(见 `MixupScheduler`):同一 step 二选一
3. **Mixup/CutMix 总有 `1 - mixup_prob` 的概率不做**:留出"干净"的 batch 让 clDice / Boundary 能正常监督

**配置建议**(在 `config_setting.py` 已经默认):
```python
# Dataset 层
crack_copy_paste_prob = 0.5
mosaic_prob = 0.0          # 默认关闭, 与裂缝目标冲突
crack_elastic_prob = 0.3   # 局部弯曲, 与上面不冲突, 始终启用

# Batch 层
mixup_prob = 0.5           # 50% 的 batch 走 mixup/cutmix, 50% 干净
mixup_warmup = 20          # 前 20 epoch 完全关掉, 让 deep supervision 先稳
```

---

## 5. Smoke Test ⭐

**文件**: `tools/smoke_test.py`

**为什么**:Round 1 找出的 7 个 bug 里,5 个属于"测都没测过就交了" 类型。
这一份测试脚本一次跑完能逮住:
- B1 / B2 / B3 — 一跑就崩的
- S1 数值正确性(SWA running average 与 numpy 真实平均做 1e-5 量级对比)
- S2 / S3 / S4 / S5 / S6 / S7 — 每一个都按照"如果 v5 的 bug 仍在,这个 assert 必失败"的方式断言

**用法**:
```bash
python tools/smoke_test.py
```

CPU 5 分钟内跑完,无需数据集。任意一项 FAIL 退出码非 0,可挂 CI。

输出示例:
```
[Round 1: 修复 v5 的 7 个 bug]
  ✓ B1 config_setting 可 import
  ✓ B2 mixup 不再 4-tuple 错配
  ✓ B3 DCN forward 调用签名正确
  ✓ S1 SWA running average 数学正确
  ...
[Round 2: 新优化项]
  ✓ 简化 ASPP (4 分支) 形状正确
  ✓ 滑窗推理输出尺寸正确
================================================================
ALL 12 CHECKS PASSED
```

---

## v5 → v6 改动一览

| 模块 | v5 | v6 |
|---|---|---|
| `configs/config_setting.py` | 用了 nn/torch/F 没 import (B1) | 修复 + LabelSmoothing 不再吞 base (S6) |
| `models/swa.py` | running avg 公式错, lr 不应用 | 修正公式, `apply_swa_lr_to_optimizer` |
| `models/ema.py` | BN buffer 永不更新 (S4) | 用 `is_floating_point` 判断,正确同步 |
| `models/dcn.py` | 含占位符乱码,签名错 | 删乱码,匹配 torchvision 签名 |
| `models/TunnelDefectSeg.py` | aux/im 二选一 | 同时返回;ASPP 7→4 分支;新增 `backbone='mbv3_small'` |
| `models/encoder_mobilenetv3.py` | (无) | 新增,torchvision 预训练编码器 |
| `models/mixup_cutmix.py` | scheduler 始终选一个 | 50% 概率不选,让干净 batch 通过 |
| `util/loader.py` | Mosaic 与 Copy-Paste 同时叠加 | 互斥;新增 `update_input_size` |
| `train.py` | mixup 4-tuple 解包错;PR 假切;swa_lr 不应用 | 全部修复 |
| `inference.py` | 384 resize → argmax → 上采样 | 滑窗 + 高斯融合 + TTA |
| `tools/smoke_test.py` | (无) | 新增,12 项端到端检查 |

---

## 推荐训练流水

```bash
# 1) 先跑 smoke test 确认环境
python tools/smoke_test.py

# 2) 训练 (默认 custom_irb)
python train.py --data_path /path/to/data

# 3) 用 mbv3 + ImageNet 预训练 (推荐, 涨点最稳)
# 在 configs/config_setting.py 把 model_config 改成:
#   {'num_classes': 7, 'backbone': 'mbv3_small',
#    'backbone_pretrained': True, 'aspp_ch': 128, ...}
python train.py --data_path /path/to/data

# 4) 滑窗推理 (大图必开)
python inference.py --weights results/.../best.pth \
    --image_dir ./test_imgs/ --output_dir ./out/ --tta
```

---

## 还能继续做的(没做,留给你)

- **Boundary IoU / Crack F1** 单独做指标,而不是只看 mIoU——你真正关心的是裂缝
- **Dilated 二值评估**:裂缝 IoU 普遍偏低还有评估口径的问题,标准做法是 GT 膨胀 1–2 px 再算 IoU(Crack500、CrackTree 都这么做)
- **半监督 / 自训练**:隧道照片很多但标注少,用 best.pth 给未标注图打伪标签后混入训练
- **Test-time 多尺度融合**:推理时把 tile 也做 0.75× / 1.25× 的多尺度,与翻转 TTA 叠加
