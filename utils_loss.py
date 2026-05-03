"""
多类语义分割损失函数 (v3 改进版)
=================================
v3 新增:
  1. LovaszSoftmaxLoss  (Berman et al., CVPR 2018)
       - 直接优化 IoU 的凸代理函数 (Lovász 延拓)
       - 相比 Dice Loss 在类别不平衡时梯度更稳定
       - 对裂缝等稀疏目标非常友好
  2. BoundaryLoss (edge-aware) 参考 Kervadec et al., MIDL 2019
       - 对预测边缘像素施加额外惩罚
       - 帮助模型精准定位裂缝边界, 减少"胖预测"
  3. 改进 CrackTopologyWrapper
       - 同时接受 LovaszSoftmax + clDice + Boundary 三项
       - 权重可独立调整

组合顺序 (v3):
  FocalLovaszLoss -> DeepSupervisionLoss -> CrackTopologyWrapper

参考文献:
  * Focal Loss: Lin et al., ICCV 2017
  * Lovász-Softmax: Berman et al., "The Lovász-Softmax loss", CVPR 2018
  * Dice Loss: Milletari et al., 3DV 2016
  * clDice: Shit et al., CVPR 2021
  * Boundary Loss: Kervadec et al., MIDL 2019
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


# ============================================================
# Dice Loss (多类)
# ============================================================
class MultiClassDiceLoss(nn.Module):
    """多类 Dice Loss, 支持类别权重"""
    def __init__(self, num_classes, smooth=1.0, ignore_index=-1, class_weights=None):
        super().__init__()
        self.num_classes = num_classes
        self.smooth = smooth
        self.ignore_index = ignore_index
        if class_weights is not None:
            self.register_buffer('class_weights',
                                 torch.as_tensor(class_weights, dtype=torch.float32))
        else:
            self.class_weights = None

    def forward(self, logits, targets):
        valid = (targets != self.ignore_index)
        t = targets.clone()
        t[~valid] = 0

        probs = F.softmax(logits, dim=1)
        t_oh = F.one_hot(t, self.num_classes).permute(0, 3, 1, 2).float()
        vmask = valid.unsqueeze(1).float()
        probs = probs * vmask
        t_oh = t_oh * vmask

        dims = (0, 2, 3)
        intersection = (probs * t_oh).sum(dim=dims)
        union = probs.sum(dim=dims) + t_oh.sum(dim=dims)
        dice_per_class = (2.0 * intersection + self.smooth) / (union + self.smooth)

        if self.class_weights is not None:
            w = self.class_weights.to(logits.device)
            return 1.0 - (dice_per_class * w).sum() / w.sum()
        return 1.0 - dice_per_class.mean()


# ============================================================
# Focal Loss (多类)
# ============================================================
class FocalLoss(nn.Module):
    """Focal Loss, gamma 越大越聚焦难样本"""
    def __init__(self, alpha=None, gamma=2.0, ignore_index=-1):
        super().__init__()
        self.gamma = gamma
        self.ignore_index = ignore_index
        if alpha is not None:
            self.register_buffer('alpha', torch.as_tensor(alpha, dtype=torch.float32))
        else:
            self.alpha = None

    def forward(self, logits, targets):
        ce = F.cross_entropy(logits, targets,
                             reduction='none', ignore_index=self.ignore_index)
        valid = (targets != self.ignore_index)
        t_safe = targets.clone()
        t_safe[~valid] = 0
        probs = F.softmax(logits, dim=1)
        pt = probs.gather(1, t_safe.unsqueeze(1)).squeeze(1).clamp(1e-7, 1.0)

        focal_weight = (1.0 - pt) ** self.gamma
        if self.alpha is not None:
            a = self.alpha.to(logits.device)
            at = a.gather(0, t_safe.view(-1)).view_as(targets)
            focal_weight = focal_weight * at

        loss = focal_weight * ce
        if valid.sum() > 0:
            return loss[valid].mean()
        return loss.mean()


# ============================================================
# [v3 新增] Lovász-Softmax Loss
# 源自: Berman et al., "The Lovász-Softmax loss: A tractable surrogate for
#      the optimization of the intersection-over-union measure in neural
#      networks", CVPR 2018
#
# 核心思想:
#   IoU 是离散函数, 不可直接优化. Lovász 延拓给出了它的凸的连续代理函数.
#   对每个类别按预测错误程度排序, 赋予 Lovász 梯度权重.
#   相比 Dice: 梯度更平滑 (特别是在类别极不平衡时), 对稀疏裂缝更稳定.
# ============================================================
def _lovasz_grad(gt_sorted):
    """Lovász 梯度 (辅助函数)"""
    n = gt_sorted.numel()
    gts = gt_sorted.sum()
    inter = gts - gt_sorted.float().cumsum(0)
    union = gts + (1.0 - gt_sorted.float()).cumsum(0)
    jaccard = 1.0 - inter / union
    if n > 1:
        jaccard[1:] = jaccard[1:] - jaccard[:-1]
    return jaccard


class LovaszSoftmaxLoss(nn.Module):
    """
    多类 Lovász-Softmax Loss.
    classes='present': 只对 batch 中实际出现的类计算 (加速 + 减少背景主导)
    """
    def __init__(self, classes='present', ignore_index=-1, class_weights=None,
                 num_classes=7):
        super().__init__()
        self.classes = classes
        self.ignore_index = ignore_index
        if class_weights is not None:
            self.register_buffer('class_weights',
                                 torch.as_tensor(class_weights, dtype=torch.float32))
        else:
            self.class_weights = None
        self.num_classes = num_classes

    def _lovasz_softmax_flat(self, probs, labels):
        """probs: (P, C), labels: (P,) - P 为有效像素数"""
        C = probs.shape[1]
        losses = []
        class_to_sum = list(range(C)) if self.classes == 'all' else \
            list(torch.unique(labels).cpu().numpy().astype(int))

        for c in class_to_sum:
            fg = (labels == c).float()
            if fg.sum() == 0:
                continue
            class_pred = probs[:, c]
            errors = (fg - class_pred).abs()
            errors_sorted, perm = torch.sort(errors, descending=True)
            fg_sorted = fg[perm]
            grad = _lovasz_grad(fg_sorted)
            loss_c = torch.dot(errors_sorted, grad)
            if self.class_weights is not None:
                w = self.class_weights.to(probs.device)
                loss_c = loss_c * w[c]
            losses.append(loss_c)

        if not losses:
            return probs.sum() * 0.0
        if self.class_weights is not None:
            return torch.stack(losses).sum() / self.class_weights.to(probs.device)[
                [c for c in class_to_sum if (labels == c).any()]
            ].sum().clamp(1e-6)
        return torch.stack(losses).mean()

    def forward(self, logits, targets):
        probs = F.softmax(logits, dim=1)  # (B, C, H, W)
        B, C, H, W = probs.shape
        probs_flat = probs.permute(0, 2, 3, 1).reshape(-1, C)  # (B*H*W, C)
        labels_flat = targets.reshape(-1)                        # (B*H*W,)

        valid = labels_flat != self.ignore_index
        probs_flat = probs_flat[valid]
        labels_flat = labels_flat[valid]

        if labels_flat.numel() == 0:
            return logits.sum() * 0.0

        return self._lovasz_softmax_flat(probs_flat, labels_flat)


# ============================================================
# [v3 新增] 边缘感知损失 (Boundary-Aware Loss)
# 参考: Kervadec et al., "Boundary loss for highly unbalanced
#       segmentation", MIDL 2019
# 简化实现: 对 GT 边缘区域的预测错误施加额外惩罚
#   边缘通过 Laplacian 近似检测 (差分形态学)
# 适用: 帮助裂缝精准定边界, 减少"胖预测"
# ============================================================
class BoundaryLoss(nn.Module):
    """
    对每个类别的 GT 边缘像素施加额外 CE 惩罚.
    边缘检测: GT_mask_dilated XOR GT_mask (1px ring)
    仅对 crack_class 默认启用; classes=None 则对所有类计算.
    """
    def __init__(self, classes=None, dilate=2, ignore_index=-1, weight=0.3):
        super().__init__()
        self.classes = classes      # None=全部, list=[class_ids]
        self.dilate = dilate
        self.ignore_index = ignore_index
        self.weight = weight

    @staticmethod
    def _get_boundary_mask(binary_mask, dilate=2):
        """binary_mask: (B, H, W) bool tensor -> boundary: (B, H, W) float"""
        m = binary_mask.float().unsqueeze(1)            # (B,1,H,W)
        dilated = F.max_pool2d(m, 2 * dilate + 1, stride=1, padding=dilate)
        eroded  = -F.max_pool2d(-m, 2 * dilate + 1, stride=1, padding=dilate)
        boundary = (dilated - eroded).squeeze(1).clamp(0, 1)
        return boundary                                 # (B,H,W)

    def forward(self, logits, targets):
        probs = F.softmax(logits, dim=1)
        valid = (targets != self.ignore_index)
        t_safe = targets.clone(); t_safe[~valid] = 0

        classes = self.classes
        if classes is None:
            classes = list(range(logits.shape[1]))

        boundary_loss = logits.new_zeros(())
        count = 0
        for c in classes:
            gt_c = (t_safe == c)                               # (B,H,W)
            if not gt_c.any():
                continue
            bnd = self._get_boundary_mask(gt_c, self.dilate)  # (B,H,W)
            pred_c = probs[:, c]                               # (B,H,W)
            # 边缘像素处的交叉熵
            err = -(gt_c.float() * (pred_c + 1e-7).log()
                    + (1 - gt_c.float()) * (1 - pred_c + 1e-7).log())
            boundary_loss = boundary_loss + (err * bnd).sum() / (bnd.sum() + 1)
            count += 1

        return boundary_loss / max(count, 1) * self.weight


# ============================================================
# [v3] 组合损失: Focal + Lovász (替换纯 Dice)
# ============================================================
class FocalLovaszLoss(nn.Module):
    """
    L = focal_weight * FocalLoss + lovasz_weight * LovaszSoftmaxLoss
    保留 Dice 作可选辅助 (dice_weight>0 时启用)
    """
    def __init__(self, num_classes, focal_weight=1.0, lovasz_weight=1.0,
                 dice_weight=0.0, gamma=2.0, class_weights=None, ignore_index=-1):
        super().__init__()
        self.focal_weight = focal_weight
        self.lovasz_weight = lovasz_weight
        self.dice_weight = dice_weight
        self.focal = FocalLoss(alpha=class_weights, gamma=gamma,
                               ignore_index=ignore_index)
        self.lovasz = LovaszSoftmaxLoss(num_classes=num_classes,
                                        class_weights=class_weights,
                                        ignore_index=ignore_index)
        if dice_weight > 0:
            self.dice = MultiClassDiceLoss(num_classes=num_classes,
                                           class_weights=class_weights,
                                           ignore_index=ignore_index)
        else:
            self.dice = None

    def forward(self, logits, targets):
        loss = (self.focal_weight * self.focal(logits, targets)
                + self.lovasz_weight * self.lovasz(logits, targets))
        if self.dice is not None:
            loss = loss + self.dice_weight * self.dice(logits, targets)
        return loss


# 兼容旧名称
FocalDiceLoss = FocalLovaszLoss


# ============================================================
# 深度监督包装器
# ============================================================
class DeepSupervisionLoss(nn.Module):
    """
    兼容 model 输出:
      - (main_logits, [ds1, ds2, ds3])  训练时 (ds3 最深, 梯度权重最大)
      - main_logits                     推理/验证时
    v3: 默认权重 (0.3, 0.4, 0.5) -- 越深权重越大, 深层语义更重要
    """
    def __init__(self, base_loss, ds_weights=(0.3, 0.4, 0.5)):
        super().__init__()
        self.base_loss = base_loss
        self.ds_weights = ds_weights

    def forward(self, output, targets):
        if isinstance(output, (tuple, list)) and len(output) == 2 \
                and isinstance(output[1], (tuple, list)):
            main_logits, ds_list = output
            loss = self.base_loss(main_logits, targets)
            for i, ds in enumerate(ds_list):
                w = self.ds_weights[i] if i < len(self.ds_weights) else 0.2
                loss = loss + w * self.base_loss(ds, targets)
            return loss
        return self.base_loss(output, targets)


# ============================================================
# Soft clDice Loss - 拓扑保持损失 (线状结构专用, 沿用 v2)
# ============================================================
class SoftClDiceLoss(nn.Module):
    """软骨架 Dice 损失 (Shit et al., CVPR 2021)"""
    def __init__(self, target_class=1, iters=5, smooth=1.0):
        super().__init__()
        self.target_class = target_class
        self.iters = iters
        self.smooth = smooth

    @staticmethod
    def _soft_erode(x):
        return -F.max_pool2d(-x, kernel_size=3, stride=1, padding=1)

    @staticmethod
    def _soft_dilate(x):
        return F.max_pool2d(x, kernel_size=3, stride=1, padding=1)

    def _soft_open(self, x):
        return self._soft_dilate(self._soft_erode(x))

    def _soft_skeleton(self, x):
        x1 = self._soft_open(x)
        skel = F.relu(x - x1)
        for _ in range(self.iters):
            x = self._soft_erode(x)
            x1 = self._soft_open(x)
            delta = F.relu(x - x1)
            skel = skel + F.relu(delta - skel * delta)
        return skel

    def forward(self, logits, targets):
        c = self.target_class
        probs = F.softmax(logits, dim=1)
        pred_c = probs[:, c:c + 1]
        true_c = (targets == c).float().unsqueeze(1)

        if true_c.sum() < 1:
            return logits.new_zeros(())

        skel_pred = self._soft_skeleton(pred_c)
        skel_true = self._soft_skeleton(true_c)
        t_prec = (skel_pred * true_c).sum() / (skel_pred.sum() + self.smooth)
        t_sens = (skel_true * pred_c).sum() / (skel_true.sum() + self.smooth)
        cl_dice = 2.0 * t_prec * t_sens / (t_prec + t_sens + self.smooth)
        return 1.0 - cl_dice


# ============================================================
# [v3 改进] 裂缝拓扑保持包装器 - 加入边缘损失
# ============================================================
class CrackTopologyWrapper(nn.Module):
    """
    在任意基础损失之外额外施加:
      - clDice (拓扑连通性)     权重: cldice_weight
      - BoundaryLoss (边缘精准) 权重: 内置在 BoundaryLoss 中
    仅作用于主输出, 不影响深度监督分支计算效率.
    """
    def __init__(self, base_loss, cldice_weight=0.5,
                 crack_class=1, iters=5,
                 boundary_weight=0.3, boundary_classes=None):
        super().__init__()
        self.base_loss = base_loss
        self.cldice_weight = cldice_weight
        self.cldice = SoftClDiceLoss(target_class=crack_class, iters=iters)
        # 边缘损失: 默认只作用于裂缝类
        bc = [crack_class] if boundary_classes is None else boundary_classes
        self.boundary = BoundaryLoss(classes=bc, weight=boundary_weight)

    def forward(self, output, targets):
        base = self.base_loss(output, targets)

        # 提取主输出
        if isinstance(output, (tuple, list)) and len(output) == 2 \
                and isinstance(output[1], (tuple, list)):
            main_logits = output[0]
        else:
            main_logits = output

        return (base
                + self.cldice_weight * self.cldice(main_logits, targets)
                + self.boundary(main_logits, targets))
