# TunnelDefectSeg v6

隧道病害多类语义分割。这是 v5 的修复 + 优化 + 评估口径重做版本,
所有改动都在三份 IMPROVEMENTS 文档里有详细说明。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 跑 smoke test(强烈建议每次环境变化后跑一次)

```bash
python tools/smoke_test.py
```

应当看到 18 项 ✓。如果有 ✗,先解决再训练。

### 3. 训练

```bash
# 默认 (custom IRB backbone)
python train.py --data_path ./data

# 推荐: ImageNet 预训练 mbv3 backbone
# 先在 configs/config_setting.py 里把 model_config 改成
#   {'num_classes': 7, 'backbone': 'mbv3_small',
#    'backbone_pretrained': True, 'aspp_ch': 128, ...}
python train.py --data_path ./data
```

### 4. 测试 (全分辨率 + Tolerance IoU + Boundary F1)

```bash
# 标准: 全分辨率滑窗 + TTA + 保存最差 30 张
python test.py --weights results/.../best.pth \
               --data_path ./data --tta --save_worst_n 30

# 多尺度 (慢一点, 上限指标)
python test.py --weights results/.../best.pth --data_path ./data \
               --tta --multi_scale 0.75 1.0 1.25

# 兼容老流程 (用于和新流程对比)
python test.py --weights results/.../best.pth \
               --data_path ./data --legacy_resize_eval
```

### 5. 推理(单图或批量)

```bash
# 单图
python inference.py --weights best.pth --image x.jpg --output r.png --tta

# 批量
python inference.py --weights best.pth --image_dir ./imgs --output_dir ./out

# 多尺度 + TTA(最准)
python inference.py --weights best.pth --image x.jpg --output r.png \
                    --tta --multi_scale 0.75 1.0 1.25
```

---

## 关键文件

| 文件 | 作用 |
|---|---|
| `train.py` | 训练入口,含 EMA / SWA / Mixup / Progressive Resizing |
| `test.py` | 测试入口,**全分辨率滑窗** + Tolerance IoU + Boundary F1 |
| `inference.py` | 推理入口,滑窗 + TTA + 多尺度 |
| `tools/smoke_test.py` | 18 项端到端冒烟测试,5 分钟 CPU 跑完 |
| `models/TunnelDefectSeg.py` | 主模型,支持 `backbone='custom_irb'` 与 `'mbv3_small'` |
| `models/encoder_mobilenetv3.py` | MobileNetV3-Small + ImageNet 预训练编码器 |
| `util/metrics.py` | 完整指标:IoU / Dice / Tolerance IoU / Boundary F1 |
| `util/engine.py` | val/test 循环,含 EMA / TTA / rich-val 变体 |
| `util/loader.py` | 数据加载,Mosaic / CopyPaste 互斥;支持 `update_input_size` |
| `configs/config_setting.py` | 训练 / 损失配置 |
| `configs/class_config.py` | 类别定义、颜色 |

---

## 文档导读

按写作顺序看:

1. **`OPTIMIZATION_REPORT.md`** — Round 1: v5 7 个 bug 的诊断与修复细节
2. **`IMPROVEMENTS_v6.md`** — Round 2: 5 项涨点优化(滑窗推理、mbv3 预训练、ASPP 简化、增强互斥、smoke test)
3. **`IMPROVEMENTS_v6_round3.md`** — Round 3: 评估口径重做(Tolerance IoU、Boundary F1、全分辨率测试、失败 case 诊断)

---

## 与原 v5 的不兼容点(注意)

1. **`models.TunnelDefectSeg.forward(return_aux=True)` 返回值变了**
   - v5: `(logits, ds_list, (aux3, aux4))` 或 `(logits, ds_list, (im3, im2))`
   - v6: `(logits, ds_list, {'aux': (a3,a4)|None, 'im': (im3,im2)|None})`
   - 用 `train.py` 的标准训练流程不受影响;自定义训练循环要相应改。

2. **`util.engine.compute_metrics`** 仍可用(完全兼容),但同名的
   `util.metrics.compute_metrics` 是更全面的版本。两者在 IoU/Dice/pixel_acc
   上数值完全一致,新模块多提供 Tolerance IoU 等。

3. **`set_seed` 默认不再开 cudnn.deterministic**。
   - v5: 训练吞吐损失 20-30%
   - v6: 默认快;要确定性结果加 `--deterministic`

---

## 训完之后的诊断流程

强烈建议跑完训练后做以下两步,获得正确的精度评估:

```bash
# 步骤 1: 同一权重,两种口径,对比看差距
python test.py --weights best.pth --legacy_resize_eval --out_dir results/legacy/
python test.py --weights best.pth --tta --save_worst_n 30 --out_dir results/full_res/

# 步骤 2: 看 results/full_res/outputs/worst_cases/ 里 30 张三联图,
# 数一下三类的比例:
#   - GT 标注本身模糊/错: 这种再优化模型也救不回, 改标注或接受
#   - 模型完全没找到裂缝:  考虑 mbv3 预训练 backbone
#   - 找到了但偏厚/碎片:    考虑 boundary loss / clDice 调权
```

具体见 `IMPROVEMENTS_v6_round3.md`。

---

## 数据集格式

按文件夹组织:

```
data/
├── crack/              # 裂缝
│   ├── images/*.jpg
│   └── masks/*.png     (二值 mask)
├── leakage_b/          # 渗水 B
│   ├── images/
│   └── masks/
├── leakage_g/
├── leakage_w/
├── lining_spalling/    # 衬砌剥落
└── segment_damage/     # 管片损伤
```

`util/loader.py` 自动按文件夹名映射到类 ID(见 `configs/class_config.py`)。
