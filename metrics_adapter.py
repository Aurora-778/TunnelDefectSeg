from __future__ import annotations

import numpy as np


def confusion_matrix_from_batch(pred, target, num_classes: int) -> np.ndarray:
    if pred.ndim == 4:
        pred = pred.argmax(axis=1)

    pred = pred.reshape(-1).astype(np.int64)
    target = target.reshape(-1).astype(np.int64)
    valid = (target >= 0) & (target < num_classes)
    idx = num_classes * target[valid] + pred[valid]
    cm = np.bincount(idx, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def metrics_from_confusion_matrix(cm: np.ndarray):
    num_classes = cm.shape[0]
    tp = np.diag(cm).astype(np.float64)
    fp = cm.sum(axis=0).astype(np.float64) - tp
    fn = cm.sum(axis=1).astype(np.float64) - tp

    denom_iou = tp + fp + fn
    denom_dice = 2 * tp + fp + fn

    ious = np.full(num_classes, np.nan, dtype=np.float64)
    dices = np.full(num_classes, np.nan, dtype=np.float64)

    valid_iou = denom_iou > 0
    valid_dice = denom_dice > 0
    ious[valid_iou] = tp[valid_iou] / denom_iou[valid_iou]
    dices[valid_dice] = (2 * tp[valid_dice]) / denom_dice[valid_dice]

    total = cm.sum()
    pixel_acc = float(tp.sum() / total) if total > 0 else 0.0
    m_iou = float(np.nanmean(ious)) if np.any(~np.isnan(ious)) else 0.0
    m_dice = float(np.nanmean(dices)) if np.any(~np.isnan(dices)) else 0.0

    return pixel_acc, m_iou, m_dice, ious.tolist(), dices.tolist()


class StreamingSegMetrics:
    def __init__(self, num_classes: int):
        self.num_classes = num_classes
        self.cm = np.zeros((num_classes, num_classes), dtype=np.int64)

    def update(self, pred, target):
        self.cm += confusion_matrix_from_batch(pred, target, self.num_classes)

    def compute(self):
        return metrics_from_confusion_matrix(self.cm)

