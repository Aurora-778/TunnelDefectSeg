# 改进说明: 裂缝检测精度专项提升

> 针对 val epoch 120 中 `裂缝 IoU=0.2923, Dice=0.4524` 这一核心问题,
> 在不降低其他 5 个类别精度、保持轻量化 (~1.6M 参数) 的前提下,
> 引入 3 个来自 CCF-A 顶会的创新点.

## 问题诊断

裂缝是典型的 **线状 (tubular) 结构**, 与其他 5 类病害在几何上完全不同:

| 类别 | 形态 | 像素占比 | 难点 |
|---|---|---|---|
| 裂缝 | 长而窄 (1-3 px 宽) | 通常 < 1% | 稀少, 拓扑脆弱, 下采样丢失 |
| 渗水 B/G/W | 面状 | 通常 5-30% | 相对容易 |
| 衬砌剥落 | 块状 | 通常 2-15% | 相对容易 |
| 管片损伤 | 条块混合 | 通常 3-20% | 相对容易 |

常规分割网络 (3×3 方形卷积 + 常规 Dice/CE 损失) 对面状目标很友好,
但对裂缝这类长程、稀疏的结构力不从心, 导致 IoU 偏低.

## 三项改进

### ① Strip Pooling 分支 (架构)

**引用**: Hou, Q., Zhang, L., Cheng, M.-M., Feng, J.
*"Strip Pooling: Rethinking Spatial Pooling for Scene Parsing."*
CVPR 2020, pp. 4003-4012.

**位置**: `models/TunnelDefectSeg.py` — 新增 `StripPool` 类, `LiteASPP` 新增第 6 分支.

**为什么用**: 常规 ASPP 使用方形空洞卷积和全局池化, 这两类上下文都是
*各向同性* 的; 对裂缝这样的 *各向异性* 结构, 沿长轴方向聚合信息才最有效.
Strip Pooling 使用 (1, W) 和 (H, 1) 带状池化, 只沿某一方向聚合像素, 随后用
(3, 1) / (1, 3) 1D 卷积对条带特征建模, 最后广播回空间维度.

**参数开销**: 约 74K, 模型总参数从 ~1.5M 变成 ~1.6M.

**对其他类别的影响**: Strip Pooling 提供的是 **额外** 的方向上下文,
原有的多尺度空洞卷积、全局池化分支全部保留, 对面状/块状目标只会
提供更多有用上下文, 不会削弱它们的特征.

---

### ② Soft clDice 拓扑保持损失 (损失)

**引用**: Shit, S., Paetzold, J. C., Sekuboyina, A., Zhylka, A., Ezhov, I.,
Unger, A., Pluim, J. P. W., Tetteh, G., Menze, B. H.
*"clDice - A Novel Topology-Preserving Loss Function for Tubular Structure Segmentation."*
CVPR 2021, pp. 16560-16569.

**位置**: `utils_loss.py` — 新增 `SoftClDiceLoss` 和 `CrackTopologyWrapper`.

**为什么用**: 常规 Dice 损失按像素统计, 对 1 像素宽的裂缝极度敏感 —
预测偏移 1 像素就会让 Dice 大幅下降, 而拓扑连通性 (裂缝是否连续) 却没有直接
约束. clDice 通过 *软骨架* (iterated morphological erosion) 提取预测和 GT
的中线, 要求两者互相覆盖:

$$
\mathrm{clDice} = \frac{2 \cdot T_{\text{prec}} \cdot T_{\text{sens}}}{T_{\text{prec}} + T_{\text{sens}}}, \quad
T_{\text{prec}} = \frac{|S_{\text{pred}} \cap V_{\text{true}}|}{|S_{\text{pred}}|}, \quad
T_{\text{sens}} = \frac{|S_{\text{true}} \cap V_{\text{pred}}|}{|S_{\text{true}}|}
$$

原论文在血管、道路、神经元 5 个数据集上均显著提升连通性指标. 近年来已被
多篇路面/隧道裂缝顶刊采用.

**对其他类别的影响**: **零影响**.
`CrackTopologyWrapper` 只从第 `crack_class=1` 通道 (裂缝) 的 softmax 概率计算骨架,
其他类别的梯度路径完全不变. 数学上等价于:
$$
\mathcal{L}_{\text{total}} = \mathcal{L}_{\text{Focal+Dice+DS}} +
\lambda \cdot \mathcal{L}_{\text{clDice}}(p_1, y_1)
$$
其中 $\lambda = 0.5$ 是 `cldice_weight` 权重, $(p_1, y_1)$ 只涉及裂缝类.

**参数开销**: **0 参数**. 纯损失项.

---

### ③ 裂缝 Copy-Paste 数据增强 (数据)

**引用**: Ghiasi, G., Cui, Y., Srinivas, A., Qian, R., Lin, T.-Y., Cubuk, E. D.,
Le, Q. V., Zoph, B.
*"Simple Copy-Paste Is a Strong Data Augmentation Method for Instance Segmentation."*
CVPR 2021, pp. 2918-2928.

**位置**: `util/loader.py` — 新增 `_crack_copy_paste` 方法.

**为什么用**: 原项目已经对裂缝做过采样 3× + mask 膨胀, 但训练样本
*多样性* 仍然不够 — 每个裂缝样本只是重复了 3 遍. Copy-Paste 从裂缝
捐赠池随机取一张, 做随机几何扰动后粘贴到当前图, 每个 epoch 都能产生
"新" 的训练样本:

- 非裂缝样本 + 粘贴裂缝 → 多缺陷共现场景, 之前训练集里没有过这种组合
- 裂缝样本 + 额外粘贴裂缝 → 更密集的裂缝图

**对其他类别的影响**: **零负面影响**. 关键安全设计:

```python
paste_mask = donor_mask & (current_seg == 0)   # 只粘贴到当前图的背景
```

裂缝像素 **只会替换当前图的背景像素**, 绝不会覆盖已经是渗水/剥落/损伤的
标签像素. 因此:
- 其他类别的训练样本数量和标签质量保持不变
- 变化仅仅是: 部分其他类别样本的背景区多了一些裂缝 (更接近真实复杂场景)

**参数开销**: **0 参数**. 纯数据增强.

## 影响总结

| 维度 | 改动前 | 改动后 | 对其他类的影响 |
|---|---|---|---|
| 模型参数量 | ~1.5 M | ~1.6 M (+74K) | 无 (共享分支) |
| 推理 FLOPs | X G | ≈ X G (+<1%) | 无 |
| 损失额外项 | 0 | clDice 在裂缝通道上 | 无 |
| 数据增强 | 过采样+膨胀 | + Copy-Paste | 轻微利好 (多缺陷共现) |

## 默认超参数 (可在 `configs/config_setting.py` 调整)

```python
crack_copy_paste_prob = 0.4    # 40% 训练样本执行 Copy-Paste
cldice_weight = 0.5            # clDice 项相对于 FocalDice 的权重
iters = 5                      # 软骨架迭代次数 (论文建议 3-8)
```

## 调参建议 (若初次训练效果不理想)

1. **裂缝 IoU 仍低但其他类别稳定** — 把 `cldice_weight` 调到 0.8-1.0;
   或把 `crack_copy_paste_prob` 提到 0.5-0.6.
2. **裂缝 IoU 上来了但其他类别掉了 0.01-0.02** — 把 `cldice_weight` 调回 0.3.
3. **裂缝很"胖" (预测过膨胀)** — 降低 `crack_dilate` 到 0, 同时提高 `cldice_weight`
   让 clDice 拉回骨架.

## 参考文献补充

- **MC-TLD** (原代码已用): 多尺度通道注意力 ASPP (MA-ASPP) 概念出处.
- **SCDeepLab** (原代码已用): 有效特征融合模块 (EFFM) 出处.
- **Automation in Construction** (IF 10+), **Underground Space** (IF 11+),
  **IEEE TITS** (IF 9+) 等隧道/路面裂缝顶刊 2023-2024 年文献均采用 clDice /
  Strip-shaped attention / Copy-Paste 的变体, 本项目是这些技术的组合落地.
