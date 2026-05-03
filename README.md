# TunnelDefectSeg — 轻量化高精度隧道病害语义分割 (改进版)

针对隧道衬砌的 6 类病害(裂缝、渗水 B/G/W、衬砌剥落、管片损伤)+ 1 类背景 = 7 类语义分割。

> **本版本针对 "裂缝 IoU 偏低" 这一痛点做了三项专项改进**:
> ① Strip Pooling 分支 (CVPR 2020) · ② Soft clDice 损失 (CVPR 2021) · ③ Crack Copy-Paste 增强 (CVPR 2021)
>
> 详见 [`IMPROVEMENTS.md`](./IMPROVEMENTS.md).

## 目录

```
TunnelDefectSeg/
├── models/
│   └── TunnelDefectSeg.py       # 主模型
├── util/
│   ├── loader.py                # 数据加载与增强
│   ├── engine.py                # 训练 / 验证 / 测试循环
│   └── onnx_convert.py          # ONNX 导出
├── data/
│   └── DataPrepare.py           # 数据准备工具(沿用原脚本)
├── configs/
│   ├── class_config.py          # 类别定义 / 颜色
│   └── config_setting.py        # 训练配置
├── utils_loss.py                # 损失函数 (Focal + Dice + 深度监督)
├── utils.py                     # 工具函数 (日志 / 优化器 / 可视化)
├── augment_data.py              # 离线数据增强 (可选)
├── train.py                     # 训练入口
├── test.py                      # 测试入口
├── inference.py                 # 单张 / 批量推理
└── requirements.txt
```

## 模型设计 (基于顶会文献的改进)

所有用到的模块都是成熟、经过验证的真实结构:

| 模块 | 来源 | 作用 |
|---|---|---|
| Inverted Residual Block (IRB) | MobileNetV2/V3 | 轻量卷积单元 |
| ECA (Efficient Channel Attention) | ECA-Net, CVPR 2020 | 近零参数通道注意力, 替代 CBAM |
| LiteASPP (MA-ASPP 简化版) | MC-TLD, 2023 | 多尺度空洞卷积 + 每分支 ECA |
| **Strip Pooling 分支** | **Hou et al., CVPR 2020** | **线状目标 (裂缝) 长程方向建模** |
| FeatureFusion (EFFM 简化版) | SCDeepLab, 2023 | 浅层细节 + 深层语义 双向融合 |
| 深度监督 | UNet++ | 训练时辅助监督, 推理时不激活 |
| Focal + Dice Loss | V-Net / RetinaNet | 应对类别不平衡 |
| **Soft clDice Loss (裂缝专用)** | **Shit et al., CVPR 2021** | **线状结构拓扑保持, 0 参数** |
| 裂缝过采样 + mask 膨胀 | Xue et al. 2022 | 缓解细长目标学习难 |
| **裂缝 Copy-Paste 增强** | **Ghiasi et al., CVPR 2021** | **稀有类数据扩充, 不覆盖其他类** |

**关键设计决定:** 编码器输出步长采用 16 而非 32,参照 MC-TLD 的做法。输入 384 × 384 时瓶颈为 24 × 24,此时 ASPP 的 rate = 6 / 12 / 18 对应的有效感受野 (13 / 25 / 37) 恰好对应小 / 中 / 大尺度目标,多尺度提取不被削弱。

## 模型规模

| 配置 | c_list | 参数量 (估计) | 适用 |
|---|---|---|---|
| **Lite** (默认) | (16, 32, 64, 96, 128) | ~1.5 M | 轻量部署 |
| Small | (24, 48, 96, 128, 160) | ~3 M | 精度优先 |

> 参数量为设计估计。请在本机训练前用 `python models/TunnelDefectSeg.py` 实测。

## 数据目录约定

```
data/
├── crack/              images/  labels/   # 裂缝
├── leakageB/           images/  labels/   # 渗水 B
├── leakageG/           images/  labels/   # 渗水 G
├── leakageW/           images/  labels/   # 渗水 W
├── lining falling off/ images/  labels/   # 衬砌剥落
└── segment damage/     images/  labels/   # 管片损伤
```

其中 `labels/` 下是二值 mask (前景 > 127 即视为该文件夹对应的类别)。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. (推荐) 先跑健全性检查, 验证改动真实可运行
python sanity_check.py

# 3. 训练 (使用默认配置)
python train.py --data_path /path/to/data

# 4. 测试
python test.py --weights results/xxx/checkpoints/best-epoch123-miou0.7890.pth \
               --data_path /path/to/data

# 5. 单张推理
python inference.py --weights best.pth --image test.jpg --output result.png

# 6. 批量推理
python inference.py --weights best.pth \
                    --image_dir ./test_imgs/ --output_dir ./results/

# 7. 导出 ONNX
python util/onnx_convert.py --weights best.pth --output model.onnx --dynamic
```

## 训练配置要点

位于 `configs/config_setting.py`:

- **分辨率 384 × 384**:保留裂缝细节,同时不致计算负担过大。
- **类别权重** `[0.2, 3.0, 1.5, 1.5, 1.5, 1.2, 1.2]`:背景降权,裂缝加权(占比极小)。
- **损失** `FocalDiceLoss` → `DeepSupervisionLoss` → `CrackTopologyWrapper` 三层嵌套;主 head + 3 深度监督 head 各用 Focal+Dice,**主 head 的裂缝通道额外加 clDice**。
- **优化器** AdamW,lr = 5e-4,weight_decay = 1e-2。
- **调度器** CosineAnnealingLR,T_max = epochs,eta_min = 1e-6。
- **裂缝专项**:
  - 过采样 3× (原有)
  - mask 膨胀 1 px (原有)
  - **Copy-Paste 0.4 概率 (新增, 仅粘到背景, 不覆盖其他类别)**
  - **clDice 权重 0.5 (新增, 零参数拓扑约束)**

## 相对于上一版本的主要变化

1. 编码器从自定义 `DoubleConvBlock` 换成 **MobileNet 风格 IRB**,显著轻量化。
2. 注意力模块从 CBAM 换成 **ECA**(参数量近乎为零)。
3. 新增 **LiteASPP**,提供多尺度上下文(原版缺失)。
4. 解码器用 **FeatureFusion** 模块替代 concat + 1×1 降维,加入 ECA。
5. 移除 `edge_head`(原版在推理时仍在跑,浪费计算)。
6. 编码器下采样从 32 改为 **16**,修复 ASPP 感受野与瓶颈分辨率失配问题。
7. 损失用 **`DeepSupervisionLoss` 包装**,训练/推理自动适配输出形式。
8. 训练代码去掉边缘辅助 loss 分支,逻辑更清晰。
9. **以 mIoU 而非 loss 选最佳模型**,与多数分割论文做法一致。

## 已知待验证事项

由于本代码生成环境无 torch,以下需用户在本机验证:

- 实测参数量与 FLOPs(默认约 1.5 M,Small 约 3 M,仅估计)。
- `batch_size` 在目标显卡上的最大值(默认 8 是保守值,16GB 显存大概率能用)。
- 是否开启 AMP (`config.amp = True`):开启后可以放大 batch,但须验证 Dice loss 在 fp16 下的数值稳定性。
