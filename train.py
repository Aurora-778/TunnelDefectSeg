"""
train.py  (修复版)
=============================================
修复内容:
  [B2] _apply_mixup_cutmix 与 seg_mixup_data 返回值匹配 (原版 4-tuple vs 2-tuple)
  [S2] 进入 SWA 阶段时把 swa_lr 真正写入 optimizer
  [S3] best_swa.pth 保存完整 state_dict (原版只存了第一个参数张量)
  [S5] Progressive Resizing 真正切分辨率 (调用 dataset.update_input_size)
  - cudnn.benchmark=True 提升训练吞吐 (原版全局关掉, 损失 20-30% 速度)
  - 简化主循环, 把 SWA 验证 / EMA 验证 抽出, 减少重复
"""
import os
import argparse
import warnings
import copy

import torch
from torch.utils.data import DataLoader
from torch.cuda.amp import GradScaler
import torch.backends.cudnn as cudnn
import torch.nn.functional as F
import numpy as np

from models.TunnelDefectSeg import TunnelDefectSeg
from models.ema import ModelEMA
from models.mixup_cutmix import seg_mixup_data, seg_cutmix_data, MixupScheduler
from models.swa import SWA, update_swa, update_bn
from models.progressive_resizing import ProgressiveResizing
from util.loader_npy import NpyDataset
from util.engine import (
    val_one_epoch, val_one_epoch_ema, val_one_epoch_tta, val_one_epoch_ema_tta,
    test_one_epoch, test_one_epoch_tta,
)
from utils import (set_seed, get_logger, log_config_info,
                   get_optimizer, get_scheduler, cal_params_flops, save_color_legend)
from configs.config_setting import setting_config

warnings.filterwarnings('ignore')


# ============================================================
# CLI
# ============================================================
def parse_args():
    p = argparse.ArgumentParser('TunnelDefectSeg v6 训练')
    p.add_argument('--data_path', type=str, default=None)
    p.add_argument('--batch_size', type=int, default=None)
    p.add_argument('--epochs', type=int, default=None)
    p.add_argument('--lr', type=float, default=None)
    p.add_argument('--gpu', type=str, default='0')
    p.add_argument('--no_ema', action='store_true')
    p.add_argument('--ema_decay', type=float, default=None)
    p.add_argument('--tta', action='store_true')
    p.add_argument('--mixup_prob', type=float, default=None)
    p.add_argument('--ema_tta_val', action='store_true')
    p.add_argument('--no_swa', action='store_true')
    p.add_argument('--swa_start', type=int, default=None)
    p.add_argument('--swa_lr', type=float, default=None)
    p.add_argument('--swa_bn_update', action='store_true')
    p.add_argument('--no_progressive', action='store_true')
    p.add_argument('--progressive_sizes', type=str, default=None,
                   help="格式: '256,40 384,80 512,120'")
    p.add_argument('--deterministic', action='store_true',
                   help='打开 cudnn.deterministic (debug 用, 训练吞吐降 20-30%)')
    return p.parse_args()


def _parse_progressive_sizes(sizes_str, default_sizes):
    if sizes_str is None:
        return default_sizes
    sizes = []
    for tok in sizes_str.strip().split():
        a, b = tok.split(',')
        sizes.append((int(a), int(b)))
    return sorted(sizes, key=lambda x: x[1]) if sizes else default_sizes


# ============================================================
# Mixup/CutMix 应用 (B2 修复)
# 注意: seg_mixup_data / seg_cutmix_data 返回 (img, soft_label),
# 不再需要外面再做 one-hot 混合
# ============================================================
def _apply_mixup_or_cutmix(images, targets, mix_type, alpha, num_classes):
    if mix_type == 'mixup':
        return seg_mixup_data(images, targets, alpha=alpha, num_classes=num_classes)
    return seg_cutmix_data(images, targets, alpha=alpha, num_classes=num_classes)


# ============================================================
# 训练 step
# ============================================================
def _train_one_epoch(train_loader, model, criterion, optimizer,
                     epoch, logger, config, scaler=None,
                     mixup_params=None, num_classes=7):
    model.train()
    loss_list = []
    print_int = max(1, config.print_interval)

    for it, (images, targets) in enumerate(train_loader):
        optimizer.zero_grad(set_to_none=True)
        images = images.cuda(non_blocking=True).float()
        targets = targets.cuda(non_blocking=True).long()

        # ---- Mixup / CutMix ----
        if mixup_params is not None:
            mix_type, alpha, _ = mixup_params
            images, targets_mixed = _apply_mixup_or_cutmix(
                images, targets, mix_type, alpha, num_classes)
        else:
            targets_mixed = None

        if scaler is not None:
            with torch.cuda.amp.autocast():
                output = model(images, return_aux=True)
                loss = _compute_loss(output, targets, targets_mixed, criterion)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.5)
            scaler.step(optimizer)
            scaler.update()
        else:
            output = model(images, return_aux=True)
            loss = _compute_loss(output, targets, targets_mixed, criterion)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.5)
            optimizer.step()

        loss_list.append(loss.item())
        if it % print_int == 0:
            lr = optimizer.param_groups[0]['lr']
            mix = '' if mixup_params is None else f', mix={mixup_params[0]}'
            msg = (f'train ep{epoch} it{it} '
                   f'loss={np.mean(loss_list):.4f} lr={lr:.6f}{mix}')
            print(msg)
            logger.info(msg)

    return float(np.mean(loss_list)) if loss_list else 0.0


def _compute_loss(output, targets, targets_mixed, criterion):
    """
    output 可能是:
      - logits  (eval 模式不会到这里)
      - (logits, ds_list)
      - (logits, ds_list, {'aux': (a3,a4)|None, 'im': (im3,im2)|None})
    targets_mixed: None 或 soft one-hot (B, C, H, W)
    """
    aux_dict = None
    if isinstance(output, (tuple, list)):
        if len(output) == 3:
            logits, ds_list, aux_dict = output
        elif len(output) == 2:
            logits, ds_list = output
        else:
            logits, ds_list = output[0], None
    else:
        logits, ds_list = output, None

    if targets_mixed is not None:
        # 软标签: KL 形式 -(t * log_softmax(p)).sum(1).mean()
        loss = -(targets_mixed * F.log_softmax(logits, dim=1)).sum(dim=1).mean()
        if ds_list:
            ds_w = (0.3, 0.4, 0.5)
            for i, ds in enumerate(ds_list):
                w = ds_w[i] if i < len(ds_w) else 0.2
                loss = loss + w * (-(targets_mixed * F.log_softmax(ds, dim=1))
                                    .sum(dim=1).mean())
    else:
        # 硬标签: 走完整的 criterion (含 clDice / Boundary / DS / Lovász)
        if ds_list is not None:
            loss = criterion((logits, ds_list), targets)
        else:
            loss = criterion(logits, targets)

    # ---- 辅助分类头 (低权重) ----
    if aux_dict is not None and aux_dict.get('aux') is not None:
        a3, a4 = aux_dict['aux']
        # 把空间标签收敛到 batch-level: 取出现频次最多的类作为图级标签
        # 简化做法: 直接用每像素 CE, 按 GAP 后的形状 (B, C) 与 majority label 比较
        with torch.no_grad():
            # (B, H, W) -> (B,) majority class
            B = targets.shape[0]
            flat = targets.view(B, -1)
            img_label = torch.mode(flat, dim=1).values
        if a3 is not None:
            loss = loss + 0.01 * F.cross_entropy(a3, img_label)
        if a4 is not None:
            loss = loss + 0.01 * F.cross_entropy(a4, img_label)

    return loss


# ============================================================
# 主入口
# ============================================================
def main():
    args = parse_args()
    config = setting_config

    # ---- CLI override ----
    if args.data_path:    config.data_path = args.data_path
    if args.batch_size:   config.batch_size = args.batch_size
    if args.epochs:       config.epochs = args.epochs
    if args.lr:           config.lr = args.lr
    if args.ema_decay:    config.ema_decay = args.ema_decay
    if args.mixup_prob is not None:
        config.mixup_prob = args.mixup_prob
    if args.swa_lr:       config.swa_lr = args.swa_lr
    if args.swa_start:    config.swa_start_epoch = args.swa_start

    use_ema = not args.no_ema
    use_tta = args.tta
    use_ema_tta_val = args.ema_tta_val
    use_swa = not args.no_swa
    use_progressive = not args.no_progressive

    # cudnn 设置
    deterministic = args.deterministic or getattr(config, 'deterministic', False)
    cudnn.benchmark = not deterministic
    cudnn.deterministic = deterministic

    swa_start = getattr(config, 'swa_start_epoch', int(config.epochs * 0.75))
    swa_lr = getattr(config, 'swa_lr', 1e-4)
    swa_bn_update = args.swa_bn_update or getattr(config, 'swa_bn_update', True)

    # Progressive Resizing
    if use_progressive:
        pr_sizes = _parse_progressive_sizes(
            args.progressive_sizes,
            getattr(config, 'progressive_resizing', None)
        )
    else:
        pr_sizes = None

    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu

    # ---- 目录 ----
    os.makedirs(config.work_dir, exist_ok=True)
    log_dir = os.path.join(config.work_dir, 'log')
    ckpt_dir = os.path.join(config.work_dir, 'checkpoints')
    out_dir = os.path.join(config.work_dir, 'outputs')
    for d in (log_dir, ckpt_dir, out_dir):
        os.makedirs(d, exist_ok=True)

    logger = get_logger('train', log_dir)
    log_config_info(config, logger)
    save_color_legend(out_dir)

    logger.info(f'cudnn.benchmark={cudnn.benchmark} deterministic={cudnn.deterministic}')
    logger.info(f'EMA={use_ema}  SWA={use_swa}  PR={pr_sizes is not None}  TTA={use_tta}')

    set_seed(config.seed, deterministic=deterministic)
    torch.cuda.empty_cache()

    # ---- PR scheduler ----
    if pr_sizes:
        pr_scheduler = ProgressiveResizing(pr_sizes)
        init_size = pr_sizes[0][0]
        logger.info(f'  Progressive resizing schedule: {pr_sizes}')
    else:
        pr_scheduler = None
        init_size = getattr(config, 'fixed_input_size', (config.input_size_h,))[0] \
                    if isinstance(getattr(config, 'fixed_input_size', None), tuple) \
                    else config.input_size_h

    # ---- 数据 ----
    logger.info('#--------- 数据集 ---------#')
    npy_dir = getattr(config, 'npy_dir', os.path.join(config.data_path, 'npy'))
    train_set = NpyDataset(npy_dir, split='train', img_size=(init_size, init_size), augment=True)
    val_set   = NpyDataset(npy_dir, split='val',   img_size=(init_size, init_size), augment=False)
    test_set  = NpyDataset(npy_dir, split='test',  img_size=(init_size, init_size), augment=False)

    train_loader = DataLoader(train_set, batch_size=config.batch_size, shuffle=True,
                              num_workers=config.num_workers, pin_memory=True,
                              drop_last=True, persistent_workers=config.num_workers > 0)
    val_loader = DataLoader(val_set, batch_size=1, shuffle=False,
                            num_workers=config.num_workers, pin_memory=True)
    test_loader = DataLoader(test_set, batch_size=1, shuffle=False,
                             num_workers=config.num_workers, pin_memory=True)
    logger.info(f'train={len(train_set)} val={len(val_set)} test={len(test_set)}')

    # ---- 模型 ----
    logger.info('#--------- 模型 ---------#')
    model = TunnelDefectSeg(**config.model_config).cuda()
    cal_params_flops(model, init_size, logger)

    ema = None
    if use_ema:
        decay = getattr(config, 'ema_decay', 0.9998)
        warmup = getattr(config, 'ema_warmup_steps', 2000)
        ema = ModelEMA(model, decay=decay, warmup_steps=warmup, device='cuda')
        logger.info(f'  EMA: decay={decay} warmup={warmup}')

    swa = None
    if use_swa:
        swa = SWA(model, swa_start=swa_start, swa_lr=swa_lr, device='cuda')
        logger.info(f'  SWA: start={swa_start} lr={swa_lr} bn_update={swa_bn_update}')

    # ---- loss / optimizer / scheduler / scaler ----
    criterion = config.criterion
    optimizer = get_optimizer(config, model)
    scheduler = get_scheduler(config, optimizer)
    scaler = GradScaler() if config.amp else None

    mixup_scheduler = MixupScheduler(
        total_epochs=config.epochs,
        warmup=getattr(config, 'mixup_warmup', 20),
        mixup_prob=getattr(config, 'mixup_prob', 0.5),
        mixup_alpha=getattr(config, 'mixup_alpha', 0.5),
        cutmix_alpha=getattr(config, 'cutmix_alpha', 1.0),
    )
    num_classes = getattr(config, 'num_classes', 7)

    # ---- 断点恢复 ----
    best_miou = 0.0
    best_miou_ema = 0.0
    best_miou_swa = 0.0
    best_epoch = 0
    start_epoch = 1
    resume_path = os.path.join(ckpt_dir, 'latest.pth')
    if os.path.exists(resume_path):
        logger.info(f'#--------- resume from {resume_path} ---------#')
        ck = torch.load(resume_path, map_location='cpu')
        model.load_state_dict(ck['model_state_dict'])
        optimizer.load_state_dict(ck['optimizer_state_dict'])
        scheduler.load_state_dict(ck['scheduler_state_dict'])
        start_epoch = ck['epoch'] + 1
        best_miou = ck.get('best_miou', 0.0)
        best_miou_ema = ck.get('best_miou_ema', 0.0)
        best_miou_swa = ck.get('best_miou_swa', 0.0)
        best_epoch = ck.get('best_epoch', 0)
        if use_ema and 'ema_state_dict' in ck and ema is not None:
            ema.load_state_dict(ck['ema_state_dict'])
        if use_swa and 'swa_state_dict' in ck and swa is not None:
            swa.load_state_dict(ck['swa_state_dict'])

    # ---- 主循环 ----
    logger.info('#--------- 开始训练 ---------#')
    swa_lr_applied = False
    for epoch in range(start_epoch, config.epochs + 1):
        torch.cuda.empty_cache()

        # ---- [S5] Progressive Resizing 真正切分辨率 ----
        if pr_scheduler is not None:
            old_size = pr_scheduler.size
            pr_scheduler.update(epoch)
            new_size = pr_scheduler.size
            if new_size != old_size:
                # 同步给所有数据集 (val/test 也跟随, 保证 shape 一致)
                for ds in (train_set, val_set, test_set):
                    ds.update_input_size(new_size, new_size)
                logger.info(f'  [PR] epoch {epoch}: 切到 {new_size}×{new_size}')

        # ---- [S2] 进入 SWA 阶段时把 swa_lr 应用到 optimizer ----
        if (swa is not None and epoch >= swa.swa_start
                and not swa_lr_applied):
            swa.apply_swa_lr_to_optimizer(optimizer)
            swa_lr_applied = True
            logger.info(f'  [SWA] 进入 SWA 阶段 @ epoch {epoch}, '
                        f'optimizer lr 锁定到 {swa_lr}')

        mixup_params = mixup_scheduler.get_params(epoch)

        # ---- 训练一轮 ----
        train_loss = _train_one_epoch(
            train_loader, model, criterion, optimizer,
            epoch, logger, config, scaler=scaler,
            mixup_params=mixup_params, num_classes=num_classes,
        )

        # ---- EMA 更新 (per-epoch 的话, 准确说应该在 step 后, 但这里
        #     是简化方式, 与 v5 一致)----
        if ema is not None:
            ema.update()

        # ---- SWA 更新 ----
        if swa is not None and epoch >= swa.swa_start:
            swa.update()

        # ---- LR scheduler step ----
        # SWA 阶段不再 step (lr 已经锁定到 swa_lr)
        if not (swa is not None and epoch >= swa.swa_start):
            scheduler.step()

        # ---- 验证 (按 val_interval) ----
        do_val = (epoch % config.val_interval == 0)
        val_miou = 0.0
        if do_val:
            if use_ema_tta_val and ema is not None:
                _, val_miou = val_one_epoch_ema_tta(
                    val_loader, model, ema, criterion, epoch, logger, config)
            elif use_tta:
                _, val_miou = val_one_epoch_tta(
                    val_loader, model, criterion, epoch, logger, config)
            else:
                _, val_miou = val_one_epoch(
                    val_loader, model, criterion, epoch, logger, config)

            # EMA 单独验证
            if ema is not None:
                _, em = val_one_epoch_ema(
                    val_loader, model, ema, criterion, epoch, logger, config)
                if em > best_miou_ema:
                    best_miou_ema = em
                    torch.save(ema.module.state_dict(),
                               os.path.join(ckpt_dir, 'best_ema.pth'))
                    logger.info(f'  ★ EMA best mIoU={best_miou_ema:.4f}')

            # SWA 单独验证 (在 SWA 阶段才有意义)
            if swa is not None and epoch >= swa.swa_start and swa.n_averaged > 0:
                # 备份, 切到 SWA 权重做评估, 再切回去
                backup = copy.deepcopy(model.state_dict())
                swa.write_back_to_model()
                _, sm = val_one_epoch(val_loader, model, criterion,
                                       epoch, logger, config)
                if sm > best_miou_swa:
                    best_miou_swa = sm
                    # [S3 修复] 保存完整 state_dict
                    torch.save(model.state_dict(),
                               os.path.join(ckpt_dir, 'best_swa.pth'))
                    logger.info(f'  ★ SWA best mIoU={best_miou_swa:.4f}')
                model.load_state_dict(backup)

        # ---- 普通最佳 ----
        if do_val and val_miou > best_miou:
            best_miou = val_miou
            best_epoch = epoch
            torch.save(model.state_dict(), os.path.join(ckpt_dir, 'best.pth'))
            logger.info(f'  ★ best mIoU={best_miou:.4f} @ ep{best_epoch}')

        # ---- latest checkpoint ----
        sd = {
            'epoch': epoch,
            'best_miou': best_miou,
            'best_miou_ema': best_miou_ema,
            'best_miou_swa': best_miou_swa,
            'best_epoch': best_epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'scheduler_state_dict': scheduler.state_dict(),
        }
        if ema is not None:
            sd['ema_state_dict'] = ema.state_dict()
        if swa is not None:
            sd['swa_state_dict'] = swa.state_dict()
        torch.save(sd, resume_path)

    # ---- 训练结束: 把 SWA 权重写回 + 重新跑 BN ----
    if swa is not None and swa.n_averaged > 0:
        logger.info(f'#--------- 应用 SWA (n={swa.n_averaged}) ---------#')
        swa.write_back_to_model()
        if swa_bn_update:
            logger.info('  重置 BN running stats ...')
            update_bn(model, train_loader, device='cuda')
        torch.save(model.state_dict(), os.path.join(ckpt_dir, 'swa_final.pth'))

    # ---- 测试 ----
    test_fn = test_one_epoch_tta if use_tta else test_one_epoch

    best_path = os.path.join(ckpt_dir, 'best.pth')
    if os.path.exists(best_path):
        logger.info('#--------- 测试 best.pth ---------#')
        model.load_state_dict(torch.load(best_path, map_location='cpu'))
        test_fn(test_loader, model, criterion, logger, config)

    ema_path = os.path.join(ckpt_dir, 'best_ema.pth')
    if use_ema and ema is not None and os.path.exists(ema_path):
        logger.info('#--------- 测试 best_ema.pth ---------#')
        ema.module.load_state_dict(torch.load(ema_path, map_location='cpu'))
        test_one_epoch_tta(test_loader, ema.module, criterion, logger, config) \
            if use_tta else test_one_epoch(test_loader, ema.module, criterion, logger, config)

    swa_path = os.path.join(ckpt_dir, 'swa_final.pth')
    if os.path.exists(swa_path):
        logger.info('#--------- 测试 swa_final.pth ---------#')
        model.load_state_dict(torch.load(swa_path, map_location='cpu'))
        test_fn(test_loader, model, criterion, logger, config)

    logger.info('#--------- DONE ---------#')
    logger.info(f'best mIoU={best_miou:.4f} @ ep{best_epoch}')
    if use_ema:
        logger.info(f'best EMA mIoU={best_miou_ema:.4f}')
    if use_swa and best_miou_swa > 0:
        logger.info(f'best SWA mIoU={best_miou_swa:.4f}')


if __name__ == '__main__':
    main()
