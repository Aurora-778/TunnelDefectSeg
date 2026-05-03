"""
训练 / 验证 / 测试引擎
==========================
保留原有 v3/v4 接口, 新增:
  - val_one_epoch_rich:  在普通 val 之上额外打印 Tolerance IoU 与 Boundary F1
                          (代价: per-image 计算, val 集多花 1-2 s)
  - 普通 val 仍然只用 streaming IoU, 不增加任何额外耗时
"""

import os
import sys
import numpy as np
from tqdm import tqdm
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from configs.class_config import NUM_CLASSES, CLASS_NAMES
from utils import save_seg_imgs

# 新指标 (来自 util.metrics)
try:
    from util.metrics import (
        compute_metrics as _compute_metrics_new,
        compute_tolerance_iou,
        compute_boundary_f1,
    )
    _METRICS_OK = True
except ImportError:
    _METRICS_OK = False

# [v4] TTA 支持
try:
    from models.tta import tta_inference, TTA_Balance
except ImportError:
    tta_inference = None
    TTA_Balance = None


# ============================================================
# 评估指标
# ============================================================
def compute_metrics(preds, targets, num_classes=NUM_CLASSES):
    """
    Args:
        preds, targets: 一维 numpy 数组 (拼接所有验证/测试像素)
    Returns:
        dict: mIoU, mDice, pixel_acc, 以及每类的 IoU/Dice
    """
    metrics = {}
    metrics['pixel_acc'] = float((preds == targets).sum()) / max(len(targets), 1)

    iou_list, dice_list = [], []
    for c in range(num_classes):
        pred_c = (preds == c)
        true_c = (targets == c)
        inter = (pred_c & true_c).sum()
        union = (pred_c | true_c).sum()

        iou = float(inter) / float(union) if union > 0 else float('nan')
        metrics[f'IoU_class{c}'] = iou
        if not np.isnan(iou):
            iou_list.append(iou)

        tp = inter
        fp = pred_c.sum() - inter
        fn = true_c.sum() - inter
        denom = 2 * tp + fp + fn
        dice = float(2 * tp) / float(denom) if denom > 0 else float('nan')
        metrics[f'Dice_class{c}'] = dice
        if not np.isnan(dice):
            dice_list.append(dice)

    metrics['mIoU'] = float(np.mean(iou_list)) if iou_list else 0.0
    metrics['mDice'] = float(np.mean(dice_list)) if dice_list else 0.0
    return metrics


def _log_per_class(metrics, logger):
    for c in range(NUM_CLASSES):
        iou = metrics.get(f'IoU_class{c}', float('nan'))
        dice = metrics.get(f'Dice_class{c}', float('nan'))
        name = CLASS_NAMES.get(c, f'class{c}')
        logger.info(f'  {name}: IoU={iou:.4f}, Dice={dice:.4f}')


# ============================================================
# 训练
# ============================================================
def train_one_epoch(train_loader, model, criterion, optimizer,
                    epoch, logger, config, scaler=None):
    model.train()
    loss_list = []

    for iter_idx, (images, targets) in enumerate(train_loader):
        optimizer.zero_grad()

        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        if scaler is not None:
            with torch.cuda.amp.autocast():
                output = model(images)
                loss = criterion(output, targets)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.5)  # v3: 更严格梯度裁剪
            scaler.step(optimizer)
            scaler.update()
        else:
            output = model(images)
            loss = criterion(output, targets)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.5)  # v3
            optimizer.step()

        loss_list.append(loss.item())
        if iter_idx % config.print_interval == 0:
            lr = optimizer.param_groups[0]['lr']
            msg = (f'train: epoch {epoch}, iter {iter_idx}, '
                   f'loss {np.mean(loss_list):.4f}, lr {lr:.6f}')
            print(msg)
            logger.info(msg)

    return float(np.mean(loss_list))


# ============================================================
# 验证
# ============================================================
@torch.no_grad()
def val_one_epoch(val_loader, model, criterion, epoch, logger, config):
    """返回 (avg_loss, mIoU) - 用 mIoU 选最佳"""
    model.eval()
    loss_list = []
    all_preds, all_targets = [], []

    for images, targets in tqdm(val_loader, desc=f'Val Epoch {epoch}'):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        logits = model(images)                            # eval 模式: 只有主输出
        loss = criterion(logits, targets)
        loss_list.append(loss.item())

        preds = torch.argmax(logits, dim=1)
        all_preds.append(preds.cpu().numpy())
        all_targets.append(targets.cpu().numpy())

    avg_loss = float(np.mean(loss_list))
    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = compute_metrics(all_preds, all_targets, NUM_CLASSES)

    msg = (f'val epoch {epoch}: loss {avg_loss:.4f}, '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}, '
           f'pixel_acc {metrics["pixel_acc"]:.4f}')
    print(msg)
    logger.info(msg)
    if epoch % config.val_interval == 0:
        _log_per_class(metrics, logger)

    return avg_loss, metrics['mIoU']


# ============================================================
# 加强版 val: 额外计算 Tolerance IoU + Boundary F1
# 仅在 val_one_epoch_rich(...) 显式调用时启用 (代价 +1-2 s/val)
# ============================================================
@torch.no_grad()
def val_one_epoch_rich(val_loader, model, criterion, epoch, logger, config,
                        crack_class=1, tolerance=2, theta=2):
    """
    在 val_one_epoch 的基础上多算两组裂缝专用指标:
      crack_tol_iou: GT 膨胀 ±tolerance px 后的 IoU
      crack_bf1:     边界 F1, 容差 theta px

    返回 (avg_loss, mIoU) — 不变, 兼容主循环;
    rich metrics 只打印到 logger.
    """
    if not _METRICS_OK:
        return val_one_epoch(val_loader, model, criterion, epoch, logger, config)

    model.eval()
    loss_list = []
    all_preds, all_targets = [], []
    crack_tol_iou_list, crack_bf1_list = [], []

    for images, targets in tqdm(val_loader, desc=f'Val-rich Epoch {epoch}'):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        logits = model(images)
        loss = criterion(logits, targets)
        loss_list.append(loss.item())

        preds = torch.argmax(logits, dim=1).cpu().numpy()
        gts = targets.cpu().numpy()
        all_preds.append(preds)
        all_targets.append(gts)

        # per-image 累加裂缝额外指标
        for p, g in zip(preds, gts):
            if (g == crack_class).any() or (p == crack_class).any():
                tol = compute_tolerance_iou(p, g, NUM_CLASSES,
                                             tolerance=tolerance,
                                             target_classes=[crack_class])
                bf = compute_boundary_f1(p, g, NUM_CLASSES,
                                          theta=theta,
                                          target_classes=[crack_class])
                if not np.isnan(tol[crack_class]):
                    crack_tol_iou_list.append(tol[crack_class])
                if not np.isnan(bf[crack_class]):
                    crack_bf1_list.append(bf[crack_class])

    avg_loss = float(np.mean(loss_list))
    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = _compute_metrics_new(all_preds, all_targets, NUM_CLASSES)

    msg = (f'val-rich ep{epoch}: loss {avg_loss:.4f}, '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}')
    print(msg); logger.info(msg)

    # 裂缝两项额外指标
    if crack_tol_iou_list:
        tol_mean = float(np.mean(crack_tol_iou_list))
        logger.info(f'  crack tolerance IoU (±{tolerance} px): {tol_mean:.4f}'
                    f'   (vs plain IoU {metrics.get("IoU_class1", 0):.4f})')
    if crack_bf1_list:
        bf_mean = float(np.mean(crack_bf1_list))
        logger.info(f'  crack boundary F1 (θ={theta}):          {bf_mean:.4f}')

    if epoch % config.val_interval == 0:
        _log_per_class(metrics, logger)
    return avg_loss, metrics['mIoU']


# ============================================================
# 测试 (老流程: 直接用 val_loader 输出尺寸)
# ============================================================
@torch.no_grad()
def test_one_epoch(test_loader, model, criterion, logger, config,
                   test_data_name=None):
    model.eval()
    loss_list = []
    all_preds, all_targets = [], []

    for i, (images, targets) in enumerate(tqdm(test_loader, desc='Testing')):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        logits = model(images)
        loss = criterion(logits, targets)
        loss_list.append(loss.item())

        preds = torch.argmax(logits, dim=1)
        all_preds.append(preds.cpu().numpy())
        all_targets.append(targets.cpu().numpy())

        if i % config.save_interval == 0:
            save_seg_imgs(images, targets, preds, i,
                          os.path.join(config.work_dir, 'outputs'),
                          test_data_name=test_data_name)

    avg_loss = float(np.mean(loss_list))
    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = compute_metrics(all_preds, all_targets, NUM_CLASSES)

    if test_data_name:
        logger.info(f'test_datasets_name: {test_data_name}')
    msg = (f'TEST: loss {avg_loss:.4f}, '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}, '
           f'pixel_acc {metrics["pixel_acc"]:.4f}')
    print(msg)
    logger.info(msg)
    _log_per_class(metrics, logger)
    return avg_loss, metrics['mIoU']


# ============================================================
# [v4 新增] EMA 模型验证
# ============================================================
@torch.no_grad()
def val_one_epoch_ema(val_loader, model, ema, criterion, epoch, logger, config):
    """
    使用 EMA 模型进行验证 (EMA 模型通常比训练快照泛化性更好).
    ema: ModelEMA 实例 (来自 models.ema)
    """
    model.eval()
    if ema is not None:
        ema.model.eval()
    loss_list = []
    all_preds, all_targets = [], []

    for images, targets in tqdm(val_loader, desc=f'Val-EMA Epoch {epoch}'):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        if ema is not None:
            logits = ema.module(images)
        else:
            logits = model(images)

        loss = criterion(logits, targets)
        loss_list.append(loss.item())
        preds = torch.argmax(logits, dim=1)
        all_preds.append(preds.cpu().numpy())
        all_targets.append(targets.cpu().numpy())

    avg_loss = float(np.mean(loss_list))
    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = compute_metrics(all_preds, all_targets, NUM_CLASSES)

    msg = (f'val-EMA epoch {epoch}: loss {avg_loss:.4f}, '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}, '
           f'pixel_acc {metrics["pixel_acc"]:.4f}')
    print(msg)
    logger.info(msg)
    return avg_loss, metrics['mIoU']


# ============================================================
# [v4 新增] TTA 验证 (Test-Time Augmentation)
# ============================================================
@torch.no_grad()
def val_one_epoch_tta(val_loader, model, criterion, epoch, logger, config,
                      tta_mode='mean'):
    """
    使用 TTA 进行验证 (HFlip + VFlip 平均).
    需要 model 输出不含深度监督 (eval 模式).
    tta_mode: 'mean' (推荐) 或 'max'
    """
    if tta_inference is None:
        print("[WARNING] TTA 模块未导入, 回退到普通验证")
        return val_one_epoch(val_loader, model, criterion, epoch, logger, config)

    model.eval()
    loss_list = []
    all_preds, all_targets = [], []

    for images, targets in tqdm(val_loader, desc=f'Val-TTA Epoch {epoch}'):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        preds, probs = tta_inference(
            model, images,
            strategy=TTA_Balance,
            tta_mode=tta_mode,
            use_ema=False,
        )
        preds = preds.squeeze(1).long()

        # 计算 loss (用普通前向, TTA 仅用于评估)
        with torch.cuda.amp.autocast(enabled=False):
            logits_raw = model(images)
            if isinstance(logits_raw, (tuple, list)):
                logits_raw = logits_raw[0]
            loss = criterion(logits_raw, targets)
        loss_list.append(loss.item())

        all_preds.append(preds.cpu().numpy())
        all_targets.append(targets.cpu().numpy())

    avg_loss = float(np.mean(loss_list))
    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = compute_metrics(all_preds, all_targets, NUM_CLASSES)

    msg = (f'val-TTA epoch {epoch}: loss {avg_loss:.4f}, '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}, '
           f'pixel_acc {metrics["pixel_acc"]:.4f}')
    print(msg)
    logger.info(msg)
    return avg_loss, metrics['mIoU']


# ============================================================
# [v4 新增] EMA + TTA 联合验证 (最高精度模式)
# ============================================================
@torch.no_grad()
def val_one_epoch_ema_tta(val_loader, model, ema, criterion, epoch,
                           logger, config, tta_mode='mean'):
    """
    EMA 模型 + TTA 增强, 验证精度最高.
    推理成本: 2x (EMA) × 2x (TTA) ≈ 4x 普通验证.
    建议每 5-10 epoch 用一次, 不需要每 epoch 都用.
    """
    if tta_inference is None:
        print("[WARNING] TTA 模块未导入, 回退到 EMA 验证")
        return val_one_epoch_ema(val_loader, model, ema, criterion, epoch, logger, config)

    model.eval()
    if ema is not None:
        ema.model.eval()
    loss_list = []
    all_preds, all_targets = [], []

    for images, targets in tqdm(val_loader, desc=f'Val-EMA+TTA Epoch {epoch}'):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        # 用 EMA 模型 + TTA
        preds, probs = tta_inference(
            model if ema is None else ema.module,
            images,
            strategy=TTA_Balance,
            tta_mode=tta_mode,
            use_ema=False,
        )
        preds = preds.squeeze(1).long()

        # loss 计算 (原始 logits)
        with torch.cuda.amp.autocast(enabled=False):
            eval_model = model if ema is None else ema.module
            logits_raw = eval_model(images)
            if isinstance(logits_raw, (tuple, list)):
                logits_raw = logits_raw[0]
            loss = criterion(logits_raw, targets)
        loss_list.append(loss.item())

        all_preds.append(preds.cpu().numpy())
        all_targets.append(targets.cpu().numpy())

    avg_loss = float(np.mean(loss_list))
    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = compute_metrics(all_preds, all_targets, NUM_CLASSES)

    msg = (f'val-EMA+TTA epoch {epoch}: loss {avg_loss:.4f}, '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}, '
           f'pixel_acc {metrics["pixel_acc"]:.4f}')
    print(msg)
    logger.info(msg)
    return avg_loss, metrics['mIoU']


# ============================================================
# [v4 新增] 测试阶段 TTA
# ============================================================
@torch.no_grad()
def test_one_epoch_tta(test_loader, model, criterion, logger, config,
                       test_data_name=None, use_ema=False, ema=None,
                       tta_mode='mean'):
    """
    测试阶段 TTA 推理 (最高精度).
    use_ema: 是否使用 EMA 模型
    ema: ModelEMA 实例
    """
    if tta_inference is None:
        print("[WARNING] TTA 未导入, 回退到普通测试")
        return test_one_epoch(test_loader, model, criterion, logger, config,
                             test_data_name=test_data_name)

    eval_model = model
    if use_ema and ema is not None:
        eval_model = ema.module

    eval_model.eval()
    loss_list = []
    all_preds, all_targets = [], []

    for i, (images, targets) in enumerate(tqdm(test_loader, desc='Testing-TTA')):
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        preds, probs = tta_inference(
            eval_model, images,
            strategy=TTA_Balance,
            tta_mode=tta_mode,
            use_ema=False,
        )
        preds = preds.squeeze(1).long()

        all_preds.append(preds.cpu().numpy())
        all_targets.append(targets.cpu().numpy())

    all_preds = np.concatenate([p.reshape(-1) for p in all_preds])
    all_targets = np.concatenate([t.reshape(-1) for t in all_targets])
    metrics = compute_metrics(all_preds, all_targets, NUM_CLASSES)

    prefix = 'EMA+TTA' if use_ema else 'TTA'
    if test_data_name:
        logger.info(f'test_datasets_name: {test_data_name}')
    msg = (f'TEST-{prefix}: '
           f'mIoU {metrics["mIoU"]:.4f}, mDice {metrics["mDice"]:.4f}, '
           f'pixel_acc {metrics["pixel_acc"]:.4f}')
    print(msg)
    logger.info(msg)
    _log_per_class(metrics, logger)
    return 0.0, metrics['mIoU']
