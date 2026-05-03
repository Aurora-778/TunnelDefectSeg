"""
util/metrics.py
==================================================
针对裂缝/线状结构的评估指标合集.

为什么要重做评估:
  * 普通 mIoU 对 1 px 偏差极敏感 — 预测沿裂缝方向偏 1 px 就直接判为 FN/FP
  * 384×384 NEAREST resize 后, 1-3 px 的细裂缝大部分消失 → GT 本身已经损失
  * 业界对裂缝/血管/道路这类线状目标的标准评估包含 Tolerance IoU 与 Boundary F1

提供的指标:
  - compute_metrics:        基础 IoU / Dice / pixel-acc (与原 engine.py 兼容)
  - compute_tolerance_iou:  把 GT 膨胀 k px 后再算 IoU (CrackTree / Crack500 标准)
  - compute_boundary_f1:    Csurka et al. 边界 F1, 容差 theta px
  - compute_per_image:      返回每张图的所有指标, 用于失败 case 排查

参考:
  * Csurka et al., "What is a good evaluation measure for semantic segmentation?", BMVC 2013
  * Maninis et al., "Deep Retinal Image Understanding", MICCAI 2016 (relaxed IoU)
  * Cheng et al., "Boundary IoU: improving object-centric image segmentation evaluation", CVPR 2021
"""
from __future__ import annotations
import warnings
import numpy as np

try:
    from scipy.ndimage import binary_dilation, binary_erosion, distance_transform_edt
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
    warnings.warn('scipy 不可用, Tolerance IoU / Boundary F1 将退化为普通 IoU')


# ============================================================
# 基础: IoU / Dice / Pixel Accuracy
# ============================================================
def compute_metrics(preds, targets, num_classes):
    """
    与原 engine.compute_metrics 完全兼容.
    Args:
        preds, targets: 一维或多维 numpy / 列表, 内部会 ravel
    Returns:
        dict: pixel_acc, mIoU, mDice, IoU_class<i>, Dice_class<i>
    """
    preds = np.asarray(preds).ravel()
    targets = np.asarray(targets).ravel()
    metrics = {'pixel_acc': float((preds == targets).sum() / max(len(targets), 1))}

    iou_list, dice_list = [], []
    for c in range(num_classes):
        p = (preds == c)
        t = (targets == c)
        inter = int((p & t).sum())
        union = int((p | t).sum())
        iou = inter / union if union > 0 else float('nan')
        metrics[f'IoU_class{c}'] = iou
        if not np.isnan(iou):
            iou_list.append(iou)

        tp = inter
        fp = int(p.sum()) - tp
        fn = int(t.sum()) - tp
        denom = 2 * tp + fp + fn
        dice = (2 * tp) / denom if denom > 0 else float('nan')
        metrics[f'Dice_class{c}'] = dice
        if not np.isnan(dice):
            dice_list.append(dice)

    metrics['mIoU'] = float(np.mean(iou_list)) if iou_list else 0.0
    metrics['mDice'] = float(np.mean(dice_list)) if dice_list else 0.0
    return metrics


# ============================================================
# Tolerance IoU - GT 膨胀 k px 后再算 IoU
#   - 对裂缝偏 1-2 px 的预测不再判 0 分
#   - 对应 Crack500 / CrackTree 的标准评估口径
# ============================================================
def compute_tolerance_iou(pred_2d, gt_2d, num_classes, tolerance=2,
                          target_classes=None):
    """
    Args:
        pred_2d, gt_2d: (H, W) 2D 数组, 单张图
        tolerance:      容差像素 (整数, 通常 1-3)
        target_classes: 仅对这些类做容差, 其他类按普通 IoU
                        默认 None = 全部类都加容差
    Returns:
        dict[class_id, iou]
    """
    pred_2d = np.asarray(pred_2d)
    gt_2d = np.asarray(gt_2d)
    assert pred_2d.shape == gt_2d.shape and pred_2d.ndim == 2
    if target_classes is None:
        target_classes = list(range(num_classes))

    out = {}
    for c in range(num_classes):
        p = (pred_2d == c)
        t = (gt_2d == c)
        if c in target_classes and tolerance > 0 and _SCIPY_OK and (p.any() or t.any()):
            # 双向膨胀: 对称容差
            t_d = binary_dilation(t, iterations=tolerance)
            p_d = binary_dilation(p, iterations=tolerance)
            # 标准 Boundary IoU 风格: 交集只算原始像素与对方膨胀的交集
            inter = int((p & t_d).sum() + (t & p_d).sum() - (p & t).sum())
            union = int((p | t).sum())  # union 不膨胀, 否则全 1
            iou = inter / union if union > 0 else float('nan')
        else:
            inter = int((p & t).sum())
            union = int((p | t).sum())
            iou = inter / union if union > 0 else float('nan')
        out[c] = iou
    return out


# ============================================================
# Boundary F1 (Csurka 2013)
#   边界像素 (mask 内但邻接非 mask) 在容差 theta 内能匹配上的比例
#   对线状结构的"边界对齐"质量比 IoU 更敏感
# ============================================================
def compute_boundary_f1(pred_2d, gt_2d, num_classes, theta=2,
                        target_classes=None):
    """
    Args:
        theta:          边界匹配容差像素
        target_classes: 默认所有类
    Returns:
        dict[class_id, f1]; 类不出现时返回 nan
    """
    pred_2d = np.asarray(pred_2d)
    gt_2d = np.asarray(gt_2d)
    assert pred_2d.shape == gt_2d.shape and pred_2d.ndim == 2
    if not _SCIPY_OK:
        return {c: float('nan') for c in range(num_classes)}
    if target_classes is None:
        target_classes = list(range(num_classes))

    out = {}
    for c in range(num_classes):
        if c not in target_classes:
            out[c] = float('nan')
            continue
        p = (pred_2d == c)
        t = (gt_2d == c)
        if not p.any() and not t.any():
            out[c] = float('nan')
            continue

        # 边界 = 类内像素中, 在 1-邻域内有非类像素的 (= mask XOR erode(mask))
        p_bnd = p & ~binary_erosion(p) if p.any() else np.zeros_like(p)
        t_bnd = t & ~binary_erosion(t) if t.any() else np.zeros_like(t)

        # 距离场: 离对方边界的最近距离
        if t_bnd.any():
            dist_to_t = distance_transform_edt(~t_bnd)
        else:
            dist_to_t = np.full(t.shape, np.inf, dtype=np.float32)
        if p_bnd.any():
            dist_to_p = distance_transform_edt(~p_bnd)
        else:
            dist_to_p = np.full(p.shape, np.inf, dtype=np.float32)

        # 匹配的边界像素
        p_match = p_bnd & (dist_to_t <= theta)
        t_match = t_bnd & (dist_to_p <= theta)

        precision = p_match.sum() / max(p_bnd.sum(), 1)
        recall    = t_match.sum() / max(t_bnd.sum(), 1)
        if precision + recall > 0:
            out[c] = float(2 * precision * recall / (precision + recall))
        else:
            out[c] = 0.0
    return out


# ============================================================
# 流式 (累加) IoU - 全分辨率多张图大数据集时省内存
# ============================================================
class StreamingIoU:
    """
    用于 test.py 在全分辨率上累加 confusion matrix.
    每张图调用 update(pred, gt), 最后 compute() 返回 mIoU 等.

    比 concat 所有 pred/gt 内存友好得多: 全分辨率 2048×2048 数据集可能 GB 量级,
    confusion matrix 始终是 (C, C) = 49 个 int.
    """
    def __init__(self, num_classes):
        self.num_classes = num_classes
        self.cm = np.zeros((num_classes, num_classes), dtype=np.int64)

    def update(self, pred_2d, gt_2d):
        pred = np.asarray(pred_2d).ravel()
        gt = np.asarray(gt_2d).ravel()
        # 屏蔽超出范围的 GT (例如 ignore_index)
        mask = (gt >= 0) & (gt < self.num_classes)
        idx = self.num_classes * gt[mask].astype(np.int64) + pred[mask].astype(np.int64)
        bc = np.bincount(idx, minlength=self.num_classes ** 2)
        self.cm += bc.reshape(self.num_classes, self.num_classes)

    def compute(self):
        cm = self.cm.astype(np.float64)
        diag = np.diag(cm)
        tp_fp = cm.sum(axis=0)            # 预测为类 c 的总数
        tp_fn = cm.sum(axis=1)            # GT 类 c 的总数
        union = tp_fp + tp_fn - diag
        iou = np.where(union > 0, diag / np.maximum(union, 1), np.nan)
        dice = np.where((tp_fp + tp_fn) > 0,
                        2 * diag / np.maximum(tp_fp + tp_fn, 1), np.nan)

        out = {'pixel_acc': float(diag.sum() / max(cm.sum(), 1))}
        for c in range(self.num_classes):
            out[f'IoU_class{c}'] = float(iou[c]) if not np.isnan(iou[c]) else float('nan')
            out[f'Dice_class{c}'] = float(dice[c]) if not np.isnan(dice[c]) else float('nan')
        valid_iou = iou[~np.isnan(iou)]
        valid_dice = dice[~np.isnan(dice)]
        out['mIoU'] = float(valid_iou.mean()) if valid_iou.size else 0.0
        out['mDice'] = float(valid_dice.mean()) if valid_dice.size else 0.0
        return out


# ============================================================
# 单图全套指标 (用于 per-image CSV)
# ============================================================
def compute_per_image(pred_2d, gt_2d, num_classes,
                      tolerance=2, theta=2,
                      crack_class=1):
    """
    Args:
        crack_class: 哪个类是裂缝, 用于裂缝专项指标
    Returns:
        dict 含: mIoU, IoU_per_class..., crack_iou, crack_tol_iou,
                 crack_bf1, crack_precision, crack_recall, crack_f1
    """
    base = compute_metrics(pred_2d, gt_2d, num_classes)
    tol = compute_tolerance_iou(pred_2d, gt_2d, num_classes,
                                 tolerance=tolerance,
                                 target_classes=[crack_class])
    bf = compute_boundary_f1(pred_2d, gt_2d, num_classes,
                              theta=theta, target_classes=[crack_class])

    p = (np.asarray(pred_2d) == crack_class)
    t = (np.asarray(gt_2d) == crack_class)
    tp = int((p & t).sum())
    fp = int(p.sum()) - tp
    fn = int(t.sum()) - tp
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)

    out = dict(base)
    out['crack_tol_iou'] = tol[crack_class]
    out['crack_bf1'] = bf[crack_class]
    out['crack_precision'] = float(prec)
    out['crack_recall'] = float(rec)
    out['crack_f1'] = float(f1)
    return out


# ============================================================
# 友好的指标格式化
# ============================================================
def format_metrics_table(metrics_dict, class_names=None):
    """格式化为多行字符串, 适合 logger.info"""
    lines = []
    lines.append(f"  pixel_acc: {metrics_dict.get('pixel_acc', 0):.4f}")
    lines.append(f"  mIoU: {metrics_dict.get('mIoU', 0):.4f}   "
                 f"mDice: {metrics_dict.get('mDice', 0):.4f}")
    if class_names is not None:
        for cid, name in class_names.items():
            iou = metrics_dict.get(f'IoU_class{cid}', float('nan'))
            dice = metrics_dict.get(f'Dice_class{cid}', float('nan'))
            lines.append(f'    {name}: IoU={iou:.4f}, Dice={dice:.4f}')
    if 'crack_tol_iou' in metrics_dict:
        lines.append(f"  crack tolerance IoU (±2 px): "
                     f"{metrics_dict['crack_tol_iou']:.4f}")
    if 'crack_bf1' in metrics_dict:
        lines.append(f"  crack boundary F1 (θ=2):    "
                     f"{metrics_dict['crack_bf1']:.4f}")
    if 'crack_f1' in metrics_dict:
        lines.append(f"  crack pixel F1:              "
                     f"{metrics_dict['crack_f1']:.4f}  "
                     f"(P={metrics_dict.get('crack_precision', 0):.3f}, "
                     f"R={metrics_dict.get('crack_recall', 0):.3f})")
    return '\n'.join(lines)
