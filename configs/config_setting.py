"""
config_setting.py  (修复版)
=============================================
修复内容:
  [B1] 缺失的 torch / nn / F import (原版会让 train.py 直接 NameError)
  [S6] LabelSmoothingCrossEntropy 默认关闭, 并改成"加性辅助 loss",
       不再把 clDice / Boundary / Lovász / DeepSupervision 全部吃掉
  - 修正 SWA 起始 epoch 与 epochs 不同步 (现在按 epochs * 0.75 动态算)
  - mixup_warmup 默认放宽到 20 epoch (原 10 太短)
"""
import os
import sys
from datetime import datetime

import torch                     # B1: 修复
import torch.nn as nn            # B1: 修复
import torch.nn.functional as F  # B1: 修复

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils_loss import (FocalLovaszLoss, DeepSupervisionLoss, CrackTopologyWrapper)
from configs.class_config import NUM_CLASSES


# ============================================================
# (S6 修复) Label Smoothing - 改为"附加项",不再吞掉被包装的损失
# 原版的 .forward 会用 smooth-CE *替代* 整个 topology_loss,
# 直接让 v3/v4 的所有损失改进失效。这里改成: base_loss + α * smooth_ce
# ============================================================
class LabelSmoothingAddon(nn.Module):
    """
    L = base_loss(output, targets) + smoothing_weight * smooth_CE(main_logits, targets)
    smoothing 默认 0,等于完全不叠;需要时设 0.05 即可。
    """
    def __init__(self, base_loss_fn, num_classes, smoothing=0.0,
                 smoothing_weight=0.5):
        super().__init__()
        self.base_loss = base_loss_fn
        self.num_classes = num_classes
        self.smoothing = smoothing
        self.smoothing_weight = smoothing_weight

    @staticmethod
    def _extract_main_logits(output):
        """从 (logits, ds_list[, aux/im]) 中提取主 logits"""
        if isinstance(output, (tuple, list)):
            return output[0]
        return output

    def forward(self, output, targets):
        # 1) 原始损失链照常计算 (clDice / Boundary / Lovász / DS / Focal 全保留)
        loss = self.base_loss(output, targets)

        # 2) 仅当 smoothing > 0 时加一个轻量的标签平滑 CE
        if self.smoothing > 0:
            logits_main = self._extract_main_logits(output)
            C = self.num_classes
            with torch.no_grad():
                t_oh = F.one_hot(targets.clamp_min(0), C).float().permute(0, 3, 1, 2)
                t_smooth = t_oh * (1 - self.smoothing) + self.smoothing / C
            log_probs = F.log_softmax(logits_main, dim=1)
            smooth_ce = -(t_smooth * log_probs).sum(dim=1).mean()
            loss = loss + self.smoothing_weight * smooth_ce
        return loss


# 顺序: 背景, 裂缝
CLASS_WEIGHTS = [0.3, 5.0]

_focal_lovasz = FocalLovaszLoss(
    num_classes=NUM_CLASSES,
    focal_weight=1.0,
    lovasz_weight=1.0,
    dice_weight=0.0,
    gamma=2.0,
    class_weights=CLASS_WEIGHTS,
    ignore_index=-1,
)

_ds_loss = DeepSupervisionLoss(
    base_loss=_focal_lovasz,
    ds_weights=(0.3, 0.4, 0.5),
)

_topology_loss = CrackTopologyWrapper(
    base_loss=_ds_loss,
    cldice_weight=0.5,
    crack_class=1,
    iters=5,
    boundary_weight=0.3,
    boundary_classes=[1],
)

# 默认 smoothing=0.0 → 完全不影响原损失链
# 想试效果可以把 smoothing 调到 0.05~0.1
_final_loss = LabelSmoothingAddon(
    base_loss_fn=_topology_loss,
    num_classes=NUM_CLASSES,
    smoothing=0.0,
    smoothing_weight=0.5,
)


class setting_config:
    network = 'TunnelDefectSeg_v6'

    # ===== 模型 =====
    # 建议 aux_classifier 与 intermediate_head 二选一; 这里默认只留 aux
    model_config = {
        'num_classes': NUM_CLASSES,
        'input_channels': 3,
        'backbone': 'mbv3_small',
        'backbone_pretrained': True,
        'aspp_ch': 128,
        'deep_supervision': True,
        'aux_classifier': True,
        'intermediate_head': False,
        'pretrained_path': None,
    }

    # ===== 数据 =====
    datasets = 'TUNNEL_DEFECT'
    data_path = './data/'
    npy_dir = './data/npy/'  # .npy 数据目录
    train_ratio = 0.7
    val_ratio = 0.15

    input_size_h = 384
    input_size_w = 384
    input_channels = 3

    # 裂缝专项
    crack_oversample = 3
    crack_dilate = 1
    crack_copy_paste_prob = 0.5
    crack_elastic_prob = 0.3
    mosaic_prob = 0.0  # 默认关闭, 避免与 Mixup/CutMix 叠加污染裂缝标签

    # ===== 损失 =====
    criterion = _final_loss

    # ===== 训练 =====
    num_classes = NUM_CLASSES
    num_workers = 0
    seed = 42
    amp = True
    deterministic = False  # debug 时才打开

    batch_size = 4
    epochs = 1000
    T_max = 1000
    swa_start_epoch = 750

    # ===== 优化器 =====
    opt = 'AdamW'
    lr = 5e-4
    betas = (0.9, 0.999)
    eps = 1e-8
    weight_decay = 1e-2

    # ===== 调度器 =====
    sch = 'WP_CosineLR'
    T_max = 200
    eta_min = 1e-6
    warm_up_epochs = 5

    # ===== EMA =====
    ema_decay = 0.9998
    ema_warmup_steps = 2000

    # ===== Mixup/CutMix =====
    mixup_prob = 0.5
    mixup_alpha = 0.5
    cutmix_alpha = 1.0
    mixup_warmup = 20  # 放宽: 让 deep supervision 先稳下来

    # ===== SWA =====
    use_swa = True
    swa_start_epoch = int(epochs * 0.75)
    swa_lr = 1e-4
    swa_bn_update = True

    # ===== Progressive Resizing =====
    # 关闭 PR 时直接用固定分辨率 (input_size_h/w);
    # 建议小数据 / 短训练用固定分辨率, 长训练再考虑 PR
    progressive_resizing = None
    fixed_input_size = (384, 384)

    # ===== 输出 =====
    work_dir = r'results\20260501_202750_v6'

    print_interval = 20
    val_interval = 5
    save_interval = 10
    threshold = 0.5
