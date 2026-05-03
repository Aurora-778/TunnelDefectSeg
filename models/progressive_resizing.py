"""
TunnelDefectSeg v5 - Progressive Resizing 训练策略
====================================================
渐进式缩放 (Progressive Resizing) 核心思想:
  训练分阶段进行 — 前期用小分辨率快速收敛，后期切换到大分辨率精细调整。
  优点:
    1. 前半段训练速度提升 30~50% (小图batch可更大)
    2. 减少过拟合风险 (小图正则化效果)
    3. 最终精度持平甚至更好 (大图finetune受益于前期快速收敛)

v5 配置:
  resize_schedule: [(256, 40), (384, 80), (512, 30)] = [(分辨率, 切换的epoch), ...]
  每个元素: (target_size, switch_at_epoch)
  到达 switch_at_epoch 时自动切换到 target_size

  原理:
    epoch 1-40: 256×256  (batch 可更大，训练快)
    epoch 41-80: 384×384 (中分辨率，适应)
    epoch 81-150: 512×512 (最终分辨率，高精度)

  对裂缝分割:
    - 小分辨率时模型学习整体结构
    - 大分辨率时精细学习裂缝宽度/边缘

参考文献:
  - "Fastai Progressive Resizing", Howard et al.
  - "Inception V3 resize training", Szegedy et al.
"""

import random
import torch
import torch.nn as nn
import torch.nn.functional as F


# ============================================================
# Progressive Resizing 调度器
# ============================================================
class ProgressiveResizing:
    """
    渐进式缩放调度器。

    Args:
        sizes:          [(size, switch_epoch), ...]
                       例如: [(256, 40), (384, 80), (512, 120)]
        input_size_h:   初始高度 (默认)
        input_size_w:   初始宽度 (默认)
    """
    def __init__(self, sizes=None, input_size_h=512, input_size_w=512):
        if sizes is None:
            # 默认: 小 → 中 → 大
            sizes = [(256, 30), (384, 60), (512, 120)]
        self.sizes = sorted(sizes, key=lambda x: x[0])  # 按 size 排序
        self.current_size = self.sizes[0][0]
        self._current_idx = 0
        self._last_epoch = 0

    def get_size(self, epoch):
        """根据当前 epoch 返回应该使用的分辨率"""
        target_size = self.sizes[0][0]  # 默认最小
        for size, switch_epoch in self.sizes:
            if epoch >= switch_epoch:
                target_size = size
        return target_size

    def should_resize(self, epoch):
        """检查是否需要切换分辨率"""
        return epoch != self._last_epoch

    def update(self, epoch):
        """在每个 epoch 开始时调用，更新分辨率"""
        new_size = self.get_size(epoch)
        if new_size != self.current_size:
            print(f'[ProgressiveResizing] Switch to {new_size}×{new_size} at epoch {epoch}')
            self.current_size = new_size
        self._last_epoch = epoch

    @property
    def size(self):
        return self.current_size

    def __repr__(self):
        return f'ProgressiveResizing(sizes={self.sizes}, current={self.current_size})'


# ============================================================
# 图像Resize工具 (带插值)
# ============================================================
def resize_to_target(image, target_h, target_w, mode='bilinear', align=False):
    """
    将图像/mask 缩放到目标分辨率。

    Args:
        image:   (B, C, H, W) Tensor
        target_h, target_w: 目标尺寸
        mode:    'bilinear' / 'nearest' (mask 用 nearest)
        align:   align_corners (分割任务通常 False)
    """
    return F.interpolate(image, size=(target_h, target_w),
                        mode=mode, align_corners=False if align else None)


def resize_for_mask(image, target_size):
    """将 mask 缩放到目标尺寸 (最近邻插值)"""
    return F.interpolate(image, size=target_size, mode='nearest')


# ============================================================
# Progressive DataLoader 包装器
# ============================================================
class ProgressiveDataLoader:
    """
    自动处理渐进式缩放下数据加载的包装器。

    用法:
        dataset = TunnelDefectDataset(...)
        loader = DataLoader(...)
        pro_loader = ProgressiveDataLoader(
            loader, dataset,
            sizes=[(256, 30), (384, 60), (512, 120)],
            input_size_h=512, input_size_w=512,
        )

        for epoch in range(1, 121):
            pro_loader.update(epoch)
            for images, targets in pro_loader:
                ...
    """
    def __init__(self, dataloader, dataset, sizes=None,
                 input_size_h=512, input_size_w=512):
        self.dataloader = dataloader
        self.dataset = dataset
        self.pr = ProgressiveResizing(sizes, input_size_h, input_size_w)
        self._current_size = (input_size_h, input_size_w)

    def update(self, epoch):
        """每 epoch 调用，更新分辨率并同步 dataset"""
        self.pr.update(epoch)
        new_size = (self.pr.size, self.pr.size)
        if new_size != self._current_size:
            self._current_size = new_size
            self.dataset.update_input_size(self.pr.size, self.pr.size)

    def __iter__(self):
        return iter(self.dataloader)

    def __len__(self):
        return len(self.dataloader)

    @property
    def size(self):
        return self._current_size


# ============================================================
# 简单测试
# ============================================================
if __name__ == '__main__':
    pr = ProgressiveResizing([(256, 30), (384, 60), (512, 120)])
    for epoch in [1, 30, 31, 60, 61, 120, 121]:
        print(f'Epoch {epoch:3d}: size={pr.get_size(epoch)}')

    # 模拟 resize
    x = torch.randn(2, 3, 512, 512)
    x256 = resize_to_target(x, 256, 256)
    x384 = resize_to_target(x, 384, 384)
    print(f'Input: {tuple(x.shape)} -> 256: {tuple(x256.shape)}, 384: {tuple(x384.shape)}')