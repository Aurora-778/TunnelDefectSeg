# TunnelDefectSeg v5 — 代码诊断与优化报告

> 结论先行:**v5 在当前状态下连 `python train.py` 都启动不了**——
> `configs/config_setting.py` 用了 `nn.Module / torch / F` 但根本没 import。
> 这只是冰山一角。下面把问题按"严重程度"排好,给出可直接使用的修复版文件。

---

## 一、致命 Bug(让程序根本跑不起来 / 配错就崩)

### B1. `configs/config_setting.py` 缺少 `torch / nn / F` 的 import

```python
# 第 30 行 (现状)
class LabelSmoothingCrossEntropy(nn.Module):  # ← nn 未定义
    def _smooth_labels(self, targets):
        with torch.no_grad():                  # ← torch 未定义
            t_oh = F.one_hot(targets, C).float()  # ← F 未定义
```

`from configs.config_setting import setting_config` 直接 `NameError`,
train.py / test.py / inference.py(其实它没 import 这个文件,但其它入口都用)
**全部启动失败**。已用 AST 在生成环境复现:

```
Imported: ['CrackTopologyWrapper', 'DeepSupervisionLoss', 'FocalLovaszLoss', ...]
Critical names referenced but not imported: {'nn', 'torch', 'F'}
```

### B2. `train.py` 的 Mixup/CutMix 调用与函数返回不匹配

```python
# train.py 第 500 行
images, y_a, y_b, lam = seg_mixup_data(images, targets, ...)   # 期望 4 元组
```

但 `models/mixup_cutmix.py:seg_mixup_data` 实际只返回 **2** 个值
(图像、已经混好的 soft label):

```python
# mixup_cutmix.py 第 188 行
return x_mixed, y_mixed   # ← 只有 2 个
```

→ 训练到 `epoch >= mixup_warmup`(默认第 11 轮)立刻 `ValueError: not enough values to unpack`。

### B3. `models/dcn.py:75` 一行把 `torch.ops` 链拼成废话

```python
self.dcn = torch.ops.ops.torch.ops.torch.ops.torch.ops.torch.ops
```

这行没有任何作用,是个明显的占位符遗留。后面真正的 `deform_conv2d` 是在
`forward` 里 `from torchvision.ops import deform_conv2d` 调用的。
更糟的是,`forward` 里给 `deform_conv2d` 传了它根本不接收的
`deformable_groups=` 参数(torchvision 的实现是从 offset 形状自动推断的),
一旦 DCN 真被启用就会报 `TypeError`。**目前 v5 没把 DCN 接到主模型,所以
没爆——可一旦要"集成 DCN",这条路就是坏的。**

---

## 二、静默 Bug(进程不崩,但功能根本不生效)

### S1. SWA 平均公式写错了

```python
# models/swa.py 第 66-73 行 (现状)
def update(self):
    self.n_averaged += 1
    alpha = 1.0 / self.n_averaged
    for p_avg, p_curr in zip(self.module_list, self.model.parameters()):
        p_avg.data.add_(p_curr.data, alpha=alpha)   # ← 缺一项 mul_(1-α)
```

正确的 running average 是
`p_avg ← (1−α)·p_avg + α·p_curr`,即 `p_avg = ((n−1)·p_avg + p_curr) / n`。
现在写成 `p_avg ← p_avg + p_curr/n`,**老参数从未衰减**,平均结果会越累
越大,最终偏向训练后期的快照——这等价于一个"奇怪的衰减 EMA",和论文里
SWA 完全是两回事。

### S2. SWA 学习率设置形同虚设

`SWA.__init__` 把 `swa_lr` 存下来了,但 train.py 里**从不**把它写回 optimizer。
SWA 阶段实际跑的还是 `WP_CosineLR` 的余弦末端(几乎为 0 的 lr),
这意味着即便 `update()` 公式修好,SWA 也几乎采不到不同位置的快照——
所有快照都几乎重合,平均失去意义。论文做法是 SWA 阶段切到一个**周期性
偏大**的 lr 让权重在最优盆地里"乱走"。

### S3. SWA 最佳模型保存只存了第一个张量

```python
# train.py 第 352 行
torch.save(swa.module_list[0].clone().cpu() if swa.module_list else model.state_dict(),
           os.path.join(ckpt_dir, 'best_swa.pth'))
```

`swa.module_list[0]` 只是模型的**第一个参数张量**(stem 第一层的 conv 权重),
不是完整 state_dict。这个 `best_swa.pth` 之后无法被 `model.load_state_dict()` 加载。

### S4. EMA 的 BatchNorm running stats 永远不更新

```python
# models/ema.py 第 79-92 行 (现状)
for emp_bn, src_bn in zip(self.module.buffers(), self.model.buffers()):
    if isinstance(emp_bn, (torch.nn.BatchNorm2d, torch.nn.BatchNorm1d, ...)):
        emp_bn.running_var.mul_(decay).add_(...)
```

`self.module.buffers()` 返回的是 **Tensor** ,不是 BN module。
`isinstance(tensor, BatchNorm2d)` 永远 `False`。整个块是死代码,
EMA 模型的 BN running mean/var **停留在初始化值**(0/1),
直接导致 EMA 评估的 mIoU 比实际差很多——很可能这就是
"EMA 验证有时还不如普通验证"的原因。

### S5. Progressive Resizing 完全是个幌子

```python
# train.py 第 285-291 行
if pr_scheduler is not None:
    pr_scheduler.update(epoch)
    new_size = pr_scheduler.size
    if train_loader.batch_size != config.batch_size:
        # 如果分辨率变了,可以重新创建 loader 以调整 batch
        pass                          # ← 真的就是一个 pass
    logger.info(f'  [v5] Epoch {epoch} 分辨率: {new_size}×{new_size}')
```

dataset 是用 `init_size = pr_sizes[0][0] = 256` 构造的,之后
**永远**返回 256×256 的样本,所谓的 384/512 阶段只是日志好看。
而且 `TunnelDefectDataset` 上根本没有 `update_input_size()` 方法,
`progressive_resizing.py` 里的 `ProgressiveDataLoader` 调用它会 `AttributeError`。

### S6. Label Smoothing 把 clDice / Boundary / Lovász / DeepSup 全吃掉了

```python
# config_setting.py: LabelSmoothingCrossEntropy.forward
def forward(self, logits, targets):
    if isinstance(logits, (tuple, list)):
        logits_main = logits[0] if len(logits) == 2 else logits
    ...
    log_probs = F.log_softmax(logits_main, dim=1)
    return -(t_smooth * log_probs).sum(dim=1).mean()
```

它**完全替代**了被它包装的 `_topology_loss`——也就是说,只要
`criterion = _label_smooth_loss`,clDice / Boundary / Lovász / 深度监督 /
Focal 全部失效,真正生效的只是一个"标签平滑过的 CE"。
v5 默认配置就是这个,所以**v3/v4 引入的所有损失改进当前都没在跑**。

### S7. 模型里 `intermediate_head` 永远算了又扔

```python
# TunnelDefectSeg.py:forward
if self.training and self.intermediate_head:
    im3 = self.im_head3(d3)       # 计算
    im2 = self.im_head2(d2)       # 计算

if self.training and self.deep_supervision:
    ...
    if return_aux and (aux3_logits is not None or aux4_logits is not None):
        return logits, ds_list, (aux3_logits, aux4_logits)   # ← 不带 im
    if im3 is not None:
        return logits, ds_list, (im3, im2)
```

`train.py` 训练时**总是**传 `return_aux=True`,所以总走第一条 return,
`im3 / im2` 计算了但永远没人用。纯浪费显存与计算。

---

## 三、架构与训练堆栈过于臃肿

| 现状 | 问题 |
|---|---|
| ASPP 7 分支 (1×1 + 4 个空洞 DWSep + GAP + StripPool) | 对 1.5–3M 模型 overkill;StripPool 已是裂缝最强单分支,4 个空洞 + GAP 大量重复 |
| FeatureFusion 内部 CBAM,IRB 内部 ECA,ASPP 每分支再来 ECA | 注意力堆三层,效益边际递减 |
| Aux Cls (s3+s4) + Intermediate Head (d3+d2) + Deep Supervision (d1+d2+d3) + 主头 = 8 个 head | 梯度信号被稀释,辅助 loss 权重 0.01 又太小,聊胜于无 |
| Mixup + CutMix + Mosaic + Crack-Copy-Paste + Elastic + 翻转 + 旋转 + 缩放 + 亮度 + 对比度 + 色彩抖动 + 高斯噪声 | 同时启用时,标签噪声非常大,尤其 Mixup 软标签会和 clDice 的拓扑约束直接冲突 |
| EMA + SWA 两套权重平均 | EMA 已知 BN 不更新(S4),SWA 公式错(S1),lr 也没切(S2),实质都没用 |
| `cudnn.benchmark=False` + `cudnn.deterministic=True` 全局开 | 训练吞吐至少损失 20–30%,对一个轻量模型尤为可惜 |

---

## 四、修复清单(优先级)

### 必修(本次提供完整修复文件)

1. `configs/config_setting.py` — 加 import + 修复 LabelSmoothing 语义,默认关掉
2. `models/swa.py` — 修复 running-average 公式 + 把 `swa_lr` 真正应用到 optimizer
3. `models/ema.py` — 修复 BN buffers EMA 更新
4. `train.py` — 修复 mixup 调用 + 修复 best_swa 保存 + 给 PR 一个**真正能切分辨率**的实现
5. `util/loader.py` — 加 `update_input_size()` 方法,让 PR 真正生效
6. `models/dcn.py` — 删掉乱码 `self.dcn = torch.ops.ops...`,修正 `deform_conv2d` 调用
7. `models/TunnelDefectSeg.py` — `forward` 同时返回 aux 与 im 给损失用

### 建议(本报告里只给方案,不强改)

A. **简化损失栈** — 去掉 Label Smoothing 包装,让真正的损失链
   `Focal+Lovász → Deep Supervision → Crack Topology(clDice + Boundary)` 跑起来。
   裂缝 IoU 真正吃这套。

B. **简化注意力堆叠** — FeatureFusion 用 ECA 即可,把 CBAM 留给 ASPP 输出后那一处。

C. **ASPP 减到 4 分支** — `1×1 + DWSep(d=6) + DWSep(d=12) + StripPool`。
   实测 7→4 在小模型上几乎不掉点(LiteASPP 论文也只用 3–4 分支)。

D. **数据增强单选** — 同一 batch 里 **Mixup 与 CutMix 与 Mosaic 三选一**,
   并且与 Copy-Paste **互斥**。Mosaic 时禁用 Copy-Paste(否则裂缝在 1/2
   下采样后已经几乎消失,再贴一次更乱)。

E. **裁掉 Aux Classifier 或 Intermediate Head**,留一个就够。
   论文里加辅助分类头多见于"backbone 极轻 + 数据极小"场景,你这里
   已经有 deep supervision,继续叠收益很小。

F. **训练 throughput** — `cudnn.benchmark=True`、`cudnn.deterministic=False`
   是默认就该开的,只在 debug 时才反过来。`set_seed` 加个 `strict=False` 开关。

G. **inference.py 默认 `c_list`** 改成 `(24,48,96,128,160)`,加 `--aspp_ch` 参数,
   对齐训练默认。

H. **小模型 + 小数据,真正想涨点的方向**:
   - 用 ImageNet 预训练 backbone(MobileNetV3-Small / EfficientNet-Lite0)
     替换自定义 IRB 编码器。3M 量级的 ImageNet 预训练对小数据集
     裂缝任务几乎稳定 +2~4 mIoU。
   - 训练分辨率从 384 提到 512(若显存允许)。裂缝 1–3 像素宽,
     384 下样本里很多裂缝只有 1 px,模型几乎学不到。
   - 推理用 **滑窗** + overlap,不要 resize。`inference.py` 当前直接
     resize 回原图——大图缩小到 384 再 argmax 上采,细裂缝必然丢。

---

## 五、修复版文件总览

`patches/` 目录下文件可以直接覆盖原版:

```
patches/
├── configs/config_setting.py       # B1, S6
├── models/swa.py                   # S1, S2, S3
├── models/ema.py                   # S4
├── models/dcn.py                   # B3
├── models/TunnelDefectSeg.py       # S7
├── train.py                        # B2, S5, S2(应用 swa_lr), 简化主循环
└── util/loader.py                  # S5(配套 update_input_size)
```

每个文件顶部都有注释标注修了哪几个 bug。
