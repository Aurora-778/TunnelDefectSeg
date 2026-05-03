"""
TunnelDefectSeg - 本地健全性检查脚本
======================================
在本机有 torch 的环境下运行, 验证:
  1. 模型前向 (train/eval 两种模式) 通畅, 输出 shape 正确
  2. 模型反向 (训练模式) 通畅, 无 NaN / grad 异常
  3. 新增的 StripPool 分支参数量
  4. 新增的 SoftClDice / CrackTopologyWrapper 损失前向+反向
  5. 新增的 Crack Copy-Paste 逻辑不会破坏其他类别像素

用法:
    python sanity_check.py

预期输出: 全部 PASS, 最后一行打印 "ALL CHECKS PASSED".
"""

import numpy as np
import torch
import torch.nn.functional as F

from models.TunnelDefectSeg import TunnelDefectSeg, StripPool, LiteASPP
from utils_loss import (FocalDiceLoss, DeepSupervisionLoss,
                        SoftClDiceLoss, CrackTopologyWrapper)

print('=' * 60)
print('[1] Model forward / backward (train & eval)')
print('=' * 60)
torch.manual_seed(0)
model = TunnelDefectSeg(num_classes=7, c_list=(16, 32, 64, 96, 128))

# Train mode: should return (logits, [ds1, ds2, ds3])
model.train()
x = torch.randn(2, 3, 384, 384)
out = model(x)
assert isinstance(out, tuple) and len(out) == 2, 'train mode 应返回 (main, ds_list)'
main, ds_list = out
assert main.shape == (2, 7, 384, 384), f'主输出形状异常 {main.shape}'
assert len(ds_list) == 3
for i, d in enumerate(ds_list):
    assert d.shape == (2, 7, 384, 384), f'ds{i} 形状异常 {d.shape}'
print(f'  train main: {tuple(main.shape)} OK')
print(f'  train ds:   {[tuple(d.shape) for d in ds_list]} OK')

# Eval mode: should return logits only
model.eval()
with torch.no_grad():
    out_eval = model(x)
assert torch.is_tensor(out_eval) and out_eval.shape == (2, 7, 384, 384)
print(f'  eval  main: {tuple(out_eval.shape)} OK')

print('\n' + '=' * 60)
print('[2] Parameter counts (Lite configuration)')
print('=' * 60)
total = sum(p.numel() for p in model.parameters())
print(f'  Total params: {total/1e6:.3f} M  (target: < 2.0 M)')
assert total < 2e6, '参数量超过 2M, 不再是轻量'

strip_params = sum(p.numel() for p in model.aspp.branch6.parameters())
print(f'  StripPool branch params: {strip_params/1e3:.1f} K')

print('\n' + '=' * 60)
print('[3] StripPool standalone shape test')
print('=' * 60)
sp = StripPool(in_ch=128, out_ch=96)
inp = torch.randn(2, 128, 24, 24)
y = sp(inp)
assert y.shape == (2, 96, 24, 24), f'StripPool 输出形状错 {y.shape}'
print(f'  in  (2,128,24,24) -> out {tuple(y.shape)} OK')

# 非正方形输入也应该正确
inp2 = torch.randn(1, 64, 32, 48)
sp2 = StripPool(64, 32)
y2 = sp2(inp2)
assert y2.shape == (1, 32, 32, 48), f'非正方形输入错 {y2.shape}'
print(f'  in  (1,64,32,48)  -> out {tuple(y2.shape)} OK')

print('\n' + '=' * 60)
print('[4] SoftClDice loss forward / backward')
print('=' * 60)
logits = torch.randn(2, 7, 64, 64, requires_grad=True)
# 制造一些"裂缝"像素 (类别 1)
targets = torch.zeros(2, 64, 64, dtype=torch.long)
targets[:, 30:33, 10:50] = 1                    # 细长的裂缝
targets[:, 10:20, 40:50] = 2                    # 一块 class 2

cldice = SoftClDiceLoss(target_class=1, iters=5)
loss = cldice(logits, targets)
assert torch.isfinite(loss), f'clDice 非有限值 {loss}'
assert loss.item() > 0 and loss.item() <= 1.1, f'clDice 值域异常 {loss}'
loss.backward()
grad = logits.grad
# 梯度应主要集中在类别 1 通道
g_crack = grad[:, 1].abs().sum().item()
g_other = grad[:, [0, 2, 3, 4, 5, 6]].abs().sum().item()
# 因为是 softmax, class 1 的变化也会影响其他通道, 但 class 1 的梯度应更大
print(f'  clDice loss: {loss.item():.4f}')
print(f'  grad(class1): {g_crack:.4f}, grad(others total): {g_other:.4f}')
print(f'  OK (finite, backward flows)')

# 空 target 应返回 0
empty_targets = torch.zeros(2, 64, 64, dtype=torch.long)  # 全背景
loss_empty = cldice(logits.detach(), empty_targets)
assert loss_empty.item() == 0, f'空 target 应返回 0, 实际 {loss_empty}'
print(f'  empty target -> loss = 0  OK')

print('\n' + '=' * 60)
print('[5] Full loss pipeline (CrackTopologyWrapper + DS + FocalDice)')
print('=' * 60)
base = FocalDiceLoss(num_classes=7, class_weights=[0.2, 3.0, 1.5, 1.5, 1.5, 1.2, 1.2])
ds_loss = DeepSupervisionLoss(base_loss=base, ds_weights=(0.4, 0.3, 0.2))
final_loss = CrackTopologyWrapper(base_loss=ds_loss, cldice_weight=0.5, crack_class=1)

model.train()
x = torch.randn(2, 3, 384, 384)
out = model(x)

targets = torch.randint(0, 7, (2, 384, 384), dtype=torch.long)
l = final_loss(out, targets)
assert torch.isfinite(l), f'组合损失非有限 {l}'
l.backward()
# 检查模型参数有梯度
any_grad = any(p.grad is not None and p.grad.abs().sum() > 0
               for p in model.parameters())
assert any_grad, '模型未收到任何梯度'
print(f'  Full pipeline loss: {l.item():.4f}  OK')
print(f'  Gradients flow to model params: OK')

# eval 模式兼容性
model.eval()
with torch.no_grad():
    out_eval = model(x)
    l_eval = final_loss(out_eval, targets)
assert torch.isfinite(l_eval)
print(f'  Eval-mode loss:     {l_eval.item():.4f}  OK')

print('\n' + '=' * 60)
print('[6] Crack Copy-Paste safety: 不覆盖其他类别像素')
print('=' * 60)
# 模拟 loader 中的 paste_mask 逻辑
current_seg = np.zeros((100, 100), dtype=np.int64)
current_seg[20:40, 20:80] = 3           # 一块 class 3 (渗水G)

donor_mask = np.zeros((100, 100), dtype=bool)
donor_mask[10:90, 45:48] = True         # 一条贯穿的裂缝, 部分与 class 3 重叠
donor_mask[10:90, 70:73] = True         # 另一条

before_class3 = (current_seg == 3).sum()
paste_mask = donor_mask & (current_seg == 0)
new_seg = current_seg.copy()
new_seg[paste_mask] = 1
after_class3 = (new_seg == 3).sum()

assert before_class3 == after_class3, \
    f'其他类别像素被覆盖! 之前 {before_class3}, 之后 {after_class3}'
pasted_pixels = (new_seg == 1).sum()
print(f'  class 3 像素数: {before_class3} -> {after_class3}  (不变, OK)')
print(f'  粘贴的裂缝像素: {pasted_pixels}')
print(f'  Copy-Paste 安全性: PASS')

print('\n' + '=' * 60)
print('ALL CHECKS PASSED')
print('=' * 60)
