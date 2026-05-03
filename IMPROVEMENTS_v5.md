# TunnelDefectSeg v5 — 改动说明

> 在 v4 (v3 优化版) 基础上新增 3 项高价值优化

---

## v5 新增特性

### 1. SWA (Stochastic Weight Averaging) ⭐

**文件**: `models/swa.py`

**核心思想**: 训练末尾（默认 epoch 75% 起）对模型权重做多节点滑动平均，使模型收敛到更平坦（flat）的最优解。

| 对比项 | EMA | SWA |
|---|---|---|
| 平均方式 | 每步参数更新时做滑动平均 | 末尾多节点采样后平均 |
| 目的 | 追踪参数轨迹的均值 | 趋向 flat minimum |
| 开启时机 | 全程开启 | 训练后期 (默认75%起) |
| 互补性 | ✅ 两者可同时开启 | ✅ 互补不冲突 |

**配置**:
```python
use_swa = True
swa_start_epoch = int(epochs * 0.75)  # 第150 epoch 开始
swa_lr = 1e-4                          # SWA 阶段固定学习率
swa_bn_update = True                   # SWA 结束后更新 BN stats
```

**预期提升**: 泛化性 +0.5~1.5% IoU，几乎零额外训练成本。

---

### 2. DCN v2 (Deformable Convolutions) ⭐

**文件**: `models/dcn.py`

**核心思想**: 标准卷积的采样点是固定的 3×3 网格，DCN 通过可学习的 offset 让采样点"弯曲变形"，更好地拟合裂缝等不规则形状。

```
标准 3×3 卷积:     DCN v2:
  ○ ○ ○              ╲ ╱
  ○ ● ○     →         ●     (采样点随裂缝形状弯曲)
  ○ ○ ○              ╱ ╲
```

**核心模块**:
- `DCNv2_Conv`: 可变形卷积层 (offset 学习 + 调制标量 m_k)
- `DeformableIRB`: 可变形倒残差块
- `DCNStage`: DCN Stage 包装器（用于替换 backbone 的 stage3/stage4）

**预期提升**: IoU +1~3%，对弯曲/不规则裂缝建模能力显著增强。

**使用方法** (待集成到主模型):
```python
from models.dcn import DeformableIRB, DCNStage

# 替换 backbone 中的 stage3
dcn_stage3 = DCNStage(
    in_ch=96, out_ch=128, num_blocks=4,
    first_stride=2, use_dcn_first=True
)
```

> ⚠️ DCN 需要 `torchvision.ops.deform_conv2d`（PyTorch ≥ 1.12）。
> 如果不可用，会自动降级为标准卷积（不影响训练）。

---

### 3. Progressive Resizing (渐进式缩放) ⭐

**文件**: `models/progressive_resizing.py`

**核心思想**: 训练分阶段进行——前期用小分辨率快速收敛，后期切换到大分辨率精细调整。

**默认调度**:
```
epoch 1-40:   256×256  (batch 可更大，训练速度↑40%)
epoch 41-80: 384×384  (中分辨率，适应阶段)
epoch 81-200: 512×512  (最终分辨率，精细调整)
```

**优势**:
- 前半段训练速度提升 30~50%（小图+大batch）
- 减少过拟合风险（小图有正则化效果）
- 最终精度持平甚至更好

**预期提升**: 训练时间节省 ~20%，最终精度 +0~1% IoU。

---

## v5 配置更新

### `configs/config_setting.py` 新增字段

```python
# v5 SWA
use_swa = True
swa_start_epoch = int(epochs * 0.75)
swa_lr = 1e-4
swa_bn_update = True

# v5 Progressive Resizing
progressive_resizing = [
    (256, 40),
    (384, 80),
    (512, 120),
]
```

### `train.py` 新增命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--no_swa` | 关闭 SWA | False |
| `--swa_start` | SWA 开始 epoch | epochs×0.75 |
| `--swa_lr` | SWA 阶段学习率 | 1e-4 |
| `--swa_bn_update` | SWA 后更新 BN | False |
| `--no_progressive` | 关闭渐进式缩放 | False |
| `--progressive_sizes` | 缩放配置 | 见上 |

---

## v5 文件清单

| 文件 | 说明 |
|---|---|
| `models/swa.py` ⭐ | SWA 实现 + BN 更新 |
| `models/dcn.py` ⭐ | DCN v2 可变形卷积 |
| `models/progressive_resizing.py` ⭐ | 渐进式缩放调度 |
| `models/__init__.py` | 暴露 v5 所有新模块 |
| `train.py` | v5 训练脚本 (SWA + PR 整合) |
| `configs/config_setting.py` | v5 配置参数 |
| `IMPROVEMENTS_v5.md` | 本文档 |

---

## 快速使用

```powershell
cd C:\Users\26822\Downloads\TunnelDefectSeg_v3

# 默认 v5 (SWA + 渐进式缩放)
python train.py

# 关闭 SWA，保留渐进式缩放
python train.py --no_swa

# 关闭渐进式缩放，保留 SWA
python train.py --no_progressive

# 完整关闭 (等价于 v4)
python train.py --no_swa --no_progressive

# 自定义 SWA 开始 epoch
python train.py --swa_start 120

# 自定义渐进式缩放配置
python train.py --progressive_sizes "256,30 384,60 512,120"

# SWA 后更新 BN (推荐)
python train.py --swa_bn_update --tta

# EMA + SWA + TTA 全开
python train.py --ema_tta_val --swa_bn_update
```

---

## v5 效果预期

| 版本 | 裂缝 IoU | mIoU | 备注 |
|---|---|---|---|
| v2 (基准) | ~0.29 | ~0.62 | 原始 |
| v3 | ~0.40 | ~0.67 | +CBAM/Lovász/Boundary |
| v4 | ~0.44 | ~0.70 | +EMA/TTA/Mixup/Aux |
| **v5 (预期)** | **~0.47~0.50** | **~0.72~0.74** | +SWA/PR/DCN |

> DCN 集成到主模型需要额外修改 `TunnelDefectSeg.py`，
> 预计在后续版本中完整集成。

---

## 参考文献

1. **SWA**: Izmailov et al., "Averaging Weights Leads to Wider Optima", UAI 2018
2. **DCN v2**: Zhu et al., "Deformable ConvNets v2", ICCV 2019
3. **Progressive Resizing**: Howard et al., fastai / "Inception V3 resize training"
