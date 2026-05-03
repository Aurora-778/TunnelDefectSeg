# 改进说明 v3: 精度进一步提升

> 在 v2 基础上 (Strip Pooling + clDice + Copy-Paste) 再叠加 4 项优化,
> 目标是将裂缝 IoU 从 ~0.29 提升到 0.40+, 整体 mIoU 提升 3-5%.

---

## 一、模型架构改进

### ① CBAM 双注意力 (替换解码器中的 ECA)
**引用**: Woo et al., *"CBAM: Convolutional Block Attention Module"*, ECCV 2018.

**位置**: `models/TunnelDefectSeg.py` — 新增 `CBAM` 类, `FeatureFusion` 改用 `CBAM`.

**为什么用**:
- ECA 仅做通道注意力 (选择"哪些特征图"), CBAM 额外做**空间注意力** (选择"哪些位置")
- 空间注意力用 7×7 卷积聚合, 感受野更大, 对裂缝的细长边界定位更精准
- 通道注意力用 avg+max 双分支 MLP, 比 ECA 更充分利用全局统计

**参数开销**: 约 +50K (解码器 4 个 FeatureFusion).

---

### ② 解码器残差精化块
**引用**: Inspired by RefineNet (Lin et al., CVPR 2017) / DoubleConv (U-Net).

**位置**: `models/TunnelDefectSeg.py` — `FeatureFusion.refine`.

**为什么用**:
- 每个 FeatureFusion 在融合之后再过一个 DW 残差块
- Identity shortcut 防止解码深层时浅层细节 (裂缝纹理) 被梯度消失

**参数开销**: 约 +20K.

---

### ③ ASPP 新增 rate=24 分支
**位置**: `models/TunnelDefectSeg.py` — `LiteASPP` 改为 7 分支.

**为什么用**:
- 输入 384, 瓶颈 24×24; rate=24 的有效感受野 ≈ 49×49
- 能感知 "更长" 的裂缝走向 (原 rate=18 只覆盖 ~37px)

**参数开销**: +约 40K.

---

### ④ Small 配置 (24, 48, 96, 128, 160) 为默认
- 参数量从 ~1.6M 增至 ~2.9M
- 在 6GB VRAM (RTX 3060) 上 batch=4, 384×384 下可正常训练
- 若 GPU 受限, 可回退到 Lite (16, 32, 64, 96, 128)

---

## 二、损失函数改进

### ⑤ Lovász-Softmax Loss (替换 Dice Loss)
**引用**: Berman et al., *"The Lovász-Softmax loss"*, CVPR 2018.

**位置**: `utils_loss.py` — 新增 `LovaszSoftmaxLoss`, `FocalLovaszLoss`.

**为什么用**:
- Dice Loss 对 batch 内稀疏类的梯度不稳定 (裂缝占比 <1% 时尤甚)
- Lovász 是 IoU 的**凸连续代理**, 梯度方向更准确地指向 IoU 改进方向
- 大量论文证明在稀疏分割 (路面裂缝、血管) 上超过 Dice

**对其他类别影响**: 正面 (所有类别都使用 Lovász, 梯度更稳定).

---

### ⑥ 边缘感知损失 (Boundary Loss)
**引用**: Kervadec et al., *"Boundary loss for highly unbalanced segmentation"*, MIDL 2019.

**位置**: `utils_loss.py` — 新增 `BoundaryLoss`, 集成进 `CrackTopologyWrapper`.

**为什么用**:
- 裂缝宽 1-3px, 边界精度至关重要
- 对 GT 边缘 ±2px 环状区域的预测错误施加额外惩罚
- 减少"胖预测" (预测裂缝比真实宽 2-4px 的常见失败模式)

**对其他类别影响**: `boundary_classes=[1]` 只对裂缝计算, **零影响**.

---

### ⑦ 深度监督权重精化 (0.3 / 0.4 / 0.5)
- v2: ds=(0.4, 0.3, 0.2) 越浅权重越大
- v3: ds=(0.3, 0.4, 0.5) 越深权重越大
- 深层特征更语义化, 给予更高梯度权重有助于 ASPP 更快收敛

---

## 三、训练策略改进

### ⑧ WP_CosineLR: 热身 + 余弦退火
- 前 5 epoch 线性热身: LR 从 0 线性增至 5e-4
- 之后余弦退火至 1e-6
- 防止 AdamW 早期步长过大导致 BN 统计不稳定 (裂缝 batch 内比例极低时尤其重要)

### ⑨ 开启 AMP (自动混合精度)
- 约节省 30-40% 显存, 允许在 6GB 上使用 Small 配置
- FP16 前向 + FP32 梯度累积

### ⑩ 梯度裁剪 max_norm 从 1.0 降至 0.5
- 与 Lovász + clDice 组合时, 损失偶尔较大, 更严格裁剪防爆炸

---

## 四、数据增强改进

### ⑪ 弹性变形 (Elastic Transform)
**引用**: Simard et al., *"Best Practices for CNNs"*, ICDAR 2003.

**位置**: `util/loader.py` — 新增 `_elastic_transform`.

**为什么用**:
- 裂缝在真实隧道中呈现弯曲走向, 弹性变形模拟这种几何多样性
- 参数 `alpha=300, sigma=12` 对应中等程度弯曲 (不破坏语义)
- 只对**含裂缝像素**的样本执行, 概率 0.3

### ⑫ Mosaic 4 图拼接
**引用**: Bochkovskiy et al., *"YOLOv4"*, arXiv 2020.

**位置**: `util/loader.py` — 新增 `_mosaic_augment`.

**为什么用**:
- 把 4 张不同类别图拼成 2×2, 人为制造"多病害共现"场景
- 增加 batch 内类别多样性, 减轻类别不平衡
- 概率 0.2 (不宜过高, 否则单图分辨率过低影响细节学习)

### ⑬ Copy-Paste 随机平移 ±H/4
- v2 裂缝直接粘贴在原坐标, 位置相对固定
- v3 额外 np.roll 随机平移, 让裂缝分布更均匀

---

## 调参建议 (v3)

| 现象 | 建议 |
|---|---|
| 裂缝 IoU 仍低, 其他类别稳定 | 提高 `cldice_weight` 到 0.8; 或增大 `crack_oversample` 到 4 |
| 裂缝 IoU 上来但边界不精准 | 提高 `boundary_weight` 到 0.5 |
| 其他类别掉了 | 降低 `mosaic_prob` 到 0.1 或关闭; 降低 `lovasz_weight` 到 0.7 |
| GPU OOM | 改用 Lite c_list; 或 batch_size=2 |
| 训练不稳定 (loss 震荡) | 降低 `lr` 到 3e-4; 增大 `warm_up_epochs` 到 10 |

## 参数对比

| 维度 | v2 | v3 |
|---|---|---|
| 模型参数 | ~1.6M (Lite 默认) | ~2.9M (Small 默认) |
| 注意力模块 | ECA | CBAM (通道+空间) |
| 主损失 | Focal + Dice | Focal + Lovász |
| 额外损失 | clDice | clDice + BoundaryLoss |
| 调度器 | CosineAnnealingLR | WP_CosineLR (5ep warmup) |
| AMP | 关 | 开 |
| 数据增强 | flip + rot + scale + CP | + elastic + mosaic + 平移CP |
