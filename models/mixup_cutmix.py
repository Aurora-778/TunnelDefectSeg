"""
v4 新增: Mixup / CutMix / GridMask 数据增强
============================================
原理:
  在 batch 内对图像/标签进行混合, 构造软标签样本.
  已被证明能有效:
    - 减轻过拟合 (减少记忆化)
    - 提升对噪声标签的鲁棒性
    - 改善边界像素预测 (CutMix 强制模型关注局部)

适用于分割:
  - Mixup:  图像线性混合 → 软分割标签
  - CutMix: 图像块裁剪粘贴 → 硬分割标签 (保留更多空间结构)
  - GridMask: 网格掩膜丢弃 (保留更多局部信息, 优于 Cutout)

v4 默认: Mixup (alpha=0.5) + CutMix (alpha=1.0) 交替使用, 概率 0.5
"""
import random
import torch
import torch.nn.functional as F
import numpy as np


# ============================================================
# Mixup (Zhang et al., ICLR 2018)
# ============================================================
def mixup_data(x, y, alpha=0.5):
    """
    对 batch 内样本随机混合.
    Args:
        x: (B, C, H, W) 图像
        y: (B, H, W) 分割标签
        alpha: Beta 分布参数
    Returns:
        mixed_x, y_a, y_b, lam
    """
    if alpha > 0:
        lam = np.random.beta(alpha, alpha)
    else:
        lam = 1.0

    batch_size = x.size(0)
    index = torch.randperm(batch_size, device=x.device)

    mixed_x = lam * x + (1 - lam) * x[index]
    return mixed_x, y, y[index], lam


# ============================================================
# CutMix (Yun et al., ICCV 2019)
# ============================================================
def rand_bbox(size, lam):
    """生成 CutMix 裁剪框 (保留原始宽高比区域)"""
    W = size[3]
    H = size[2]
    cut_rat = np.sqrt(1. - lam)
    cut_w = int(W * cut_rat)
    cut_h = int(H * cut_rat)

    # 裁剪框中心
    cx = np.random.randint(W)
    cy = np.random.randint(H)

    bbx1 = np.clip(cx - cut_w // 2, 0, W)
    bby1 = np.clip(cy - cut_h // 2, 0, H)
    bbx2 = np.clip(cx + cut_w // 2, 0, W)
    bby2 = np.clip(cy + cut_h // 2, 0, H)

    return bbx1, bby1, bbx2, bby2


def cutmix_data(x, y, alpha=1.0):
    """
    切割 x 的某个区域, 用另一样本对应区域替换.
    分割标签按面积加权混合.
    """
    if alpha > 0:
        lam = np.random.beta(alpha, alpha)
    else:
        lam = 1.0

    batch_size = x.size(0)
    index = torch.randperm(batch_size, device=x.device)

    bbx1, bby1, bbx2, bby2 = rand_bbox(x.size(), lam)
    x_mixed = x.clone()
    x_mixed[:, :, bby1:bby2, bbx1:bbx2] = x[index, :, bby1:bby2, bbx1:bbx2]

    # 实际面积比例
    lam = 1 - ((bbx2 - bbx1) * (bby2 - bby1) / (x.size(-1) * x.size(-2)))
    return x_mixed, y, y[index], lam


# ============================================================
# GridMask (Chen et al., 2020) — v4 新增
# 相比 Cutout/CutMix: 保留更多局部信息, 强制模型学习全局上下文
# ============================================================
class GridMask:
    """
    网格掩膜数据增强.
    参数:
        d1, d2:    mask 边长范围 (相对于图像尺寸的 ratio)
        ratio:     mask 宽高比
        max_angle: 旋转角度
        mode:      'erase' (填0) 或 'mixed' (填另一样本)
        prob:      应用概率
    """
    def __init__(self, d1=0.4, d2=0.6, ratio=0.6, max_angle=4,
                 mode='erase', prob=0.3):
        self.d1 = d1
        self.d2 = d2
        self.ratio = ratio
        self.max_angle = max_angle
        self.mode = mode
        self.prob = prob

    def __call__(self, x, y=None):
        if random.random() > self.prob:
            return x, y

        B, C, H, W = x.shape
        d = int(H * random.uniform(self.d1, self.d2))
        l = max(int(d * self.ratio), 1)

        mask = torch.ones((H, W), device=x.device, dtype=torch.float32)
        st_h = random.randint(0, H)
        st_w = random.randint(0, W)
        for h in range(st_h - d, st_h + d, l):
            for w in range(st_w - d, st_w + d, l):
                h1, w1 = max(0, h), max(0, w)
                h2, w2 = min(H, h + d), min(W, w + d)
                mask[h1:h2, w1:w2] = 0.0

        # 随机旋转
        angle = random.uniform(-self.max_angle, self.max_angle)
        if angle != 0:
            mask = self._rotate_mask(mask.unsqueeze(0), angle).squeeze(0)

        mask = mask.unsqueeze(0).unsqueeze(0)  # (1,1,H,W)
        mask = mask.expand(B, C, H, W)

        if self.mode == 'erase':
            return x * mask, y
        else:  # mixed mode
            idx = torch.randperm(B, device=x.device)
            x_mix = x * mask + x[idx] * (1 - mask)
            return x_mix, y

    @staticmethod
    def _rotate_mask(x, angle):
        """近似旋转 (双线性插值)"""
        angle_rad = angle * np.pi / 180
        B, C, H, W = x.shape
        theta = torch.tensor([[[np.cos(angle_rad), -np.sin(angle_rad), 0],
                                [np.sin(angle_rad),  np.cos(angle_rad), 0]]],
                             device=x.device, dtype=torch.float32)
        grid = F.affine_grid(theta.expand(B, -1, -1), x.size(), align_corners=False)
        return F.grid_sample(x, grid, align_corners=False, mode='nearest')


# ============================================================
# 分割专用的 Mixup/CutMix (标签也是软混合)
# ============================================================
def seg_mixup_data(x, y, alpha=0.5, num_classes=7):
    """
    分割任务的 Mixup — 标签也是软混合 (one-hot 加权).
    Args:
        x:  (B, C, H, W)
        y:  (B, H, W) 类别索引
        alpha: Beta 分布参数
    Returns:
        mixed_x, mixed_y_soft
    """
    if alpha > 0:
        lam = np.random.beta(alpha, alpha)
    else:
        lam = 1.0

    index = torch.randperm(x.size(0), device=x.device)

    x_mixed = lam * x + (1 - lam) * x[index]

    # one-hot 标签混合
    y_oh = F.one_hot(y, num_classes=num_classes).float()   # (B,H,W,C)
    y_oh = y_oh.permute(0, 3, 1, 2)                       # (B,C,H,W)
    y_mixed = lam * y_oh + (1 - lam) * y_oh[index]

    return x_mixed, y_mixed


def seg_cutmix_data(x, y, alpha=1.0, num_classes=7):
    """
    分割任务的 CutMix — 图像块 + 软标签.
    """
    if alpha > 0:
        lam = np.random.beta(alpha, alpha)
    else:
        lam = 1.0

    index = torch.randperm(x.size(0), device=x.device)
    bbx1, bby1, bbx2, bby2 = rand_bbox(x.size(), lam)

    x_mixed = x.clone()
    x_mixed[:, :, bby1:bby2, bbx1:bbx2] = x[index, :, bby1:bby2, bbx1:bbx2]

    # 软标签 (按面积加权)
    y_oh = F.one_hot(y, num_classes=num_classes).float()
    y_oh = y_oh.permute(0, 3, 1, 2)
    y_mixed = y_oh.clone()
    y_mixed[:, :, bby1:bby2, bbx1:bbx2] = y_oh[index, :, bby1:bby2, bbx1:bbx2]

    lam = 1 - ((bbx2 - bbx1) * (bby2 - bby1) / (x.size(-1) * x.size(-2)))
    return x_mixed, y_mixed


# ============================================================
# (优化) 互斥的增强调度器
# 原版: Mixup 或 CutMix 二选一, 但 Mosaic / Copy-Paste 在 loader 里
# 各自独立判断, 同 batch 可能 4 种增强同时叠加 → 标签噪声爆炸.
#
# 新版: 每次 step 给出一个互斥决策, 总概率不超 1.
#   * 'none' (无增强)
#   * 'mixup'
#   * 'cutmix'
# Mosaic 由 dataset 自己抽签, 但 dataset 用 mixup_prob/cutmix_prob 时
# 应当先关闭自己的 mosaic_prob (见 train.py 配套使用)
# ============================================================
class MixupScheduler:
    """
    自适应切换 Mixup / CutMix:
      epoch < warmup:        无增强 (深度监督先稳)
      早期 (< 0.5*total):     倾向 Mixup
      后期 (>= 0.5*total):    倾向 CutMix

    关键: 总会以 (1 - mixup_prob) 概率返回 None (不做 batch 级混合)
    避免与 dataset 内的 Mosaic / Copy-Paste 死叠.
    """
    def __init__(self, total_epochs, warmup=20, mixup_prob=0.5,
                 mixup_alpha=0.5, cutmix_alpha=1.0):
        self.total = total_epochs
        self.warmup = warmup
        self.mixup_prob = mixup_prob          # 决定 "做不做 batch 级混合"
        self.mixup_alpha = mixup_alpha
        self.cutmix_alpha = cutmix_alpha

    def get_params(self, epoch):
        """返回 ('mixup'|'cutmix', alpha, 1.0) 或 None"""
        if epoch < self.warmup:
            return None
        if random.random() >= self.mixup_prob:
            return None  # 跳过, 仍可能在 dataset 端做 Mosaic / Copy-Paste

        progress = epoch / max(1, self.total)
        if progress < 0.5:
            return ('mixup', self.mixup_alpha, 1.0)
        # 后期 cutmix 更主导
        cutmix_p = 0.5 + (progress - 0.5)  # 0.5 → 1.0
        if random.random() < cutmix_p:
            return ('cutmix', self.cutmix_alpha, 1.0)
        return ('mixup', self.mixup_alpha, 1.0)


# ============================================================
# 快捷函数
# ============================================================
def apply_mixup_or_cutmix(x, y, num_classes=7,
                           mixup_alpha=0.5, cutmix_alpha=1.0,
                           use_mixup_prob=0.5):
    """
    根据概率随机选择 Mixup 或 CutMix.
    适合放在 DataLoader 的 collate_fn 中.
    """
    r = random.random()
    if r < use_mixup_prob:
        return seg_mixup_data(x, y, alpha=mixup_alpha, num_classes=num_classes)
    elif r < use_mixup_prob + (1 - use_mixup_prob):
        return seg_cutmix_data(x, y, alpha=cutmix_alpha, num_classes=num_classes)
    return x, y


if __name__ == '__main__':
    print("=== Mixup/CutMix 功能测试 ===")
    x = torch.randn(4, 3, 384, 384)
    y = torch.randint(0, 7, (4, 384, 384))

    # Mixup
    xm, ya, yb, lam = mixup_data(x, y, alpha=0.5)
    print(f"Mixup:  λ={lam:.3f}, x_mix shape={xm.shape}")

    # CutMix
    xc, ya, yb, lam = cutmix_data(x, y, alpha=1.0)
    print(f"CutMix: λ={lam:.3f}, x_cut shape={xc.shape}")

    # Seg Mixup
    xm, ym = seg_mixup_data(x, y, alpha=0.5, num_classes=7)
    print(f"Seg Mixup: y_soft shape={ym.shape}, sum={ym.sum():.1f} (期望≈{4*384*384})")

    # GridMask
    gm = GridMask(d1=0.3, d2=0.5, prob=1.0)
    xg, _ = gm(x, y)
    print(f"GridMask: 输出 shape={xg.shape}")
