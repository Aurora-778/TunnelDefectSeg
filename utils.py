"""
工具函数: 日志、优化器、调度器、可视化、参数统计
==================================================
本版相对原 v5 的修复:
  - set_seed 不再强制 cudnn.benchmark=False / deterministic=True
    (训练吞吐损失 20-30%, 仅 debug 时需要; 通过参数 deterministic=False 默认开 benchmark)
  - get_logger 增加 fresh=True 选项, 重训时清空旧日志; 默认追加, 与原行为一致
  - cal_params_flops 在 CPU / 无 thop 时不再崩, 静默退化
  - log_config_info 跳过不可序列化属性 (如 criterion 是 nn.Module 时), 避免日志超长
"""

import os
import math
import random
import logging
import logging.handlers
import numpy as np
import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
from matplotlib import pyplot as plt

from configs.class_config import NUM_CLASSES, CLASS_NAMES, CLASS_COLORS, mask_to_color


# ============================================================
# 种子
# ============================================================
def set_seed(seed, deterministic=False):
    """
    Args:
        seed: 整数随机种子
        deterministic: True 时强制 cudnn 确定性 (训练慢 20-30%, 仅 debug 用)
                       False 时启用 benchmark, 训练吞吐最高
    """
    os.environ['PYTHONHASHSEED'] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        cudnn.benchmark = False
        cudnn.deterministic = True
    else:
        cudnn.benchmark = True
        cudnn.deterministic = False


# ============================================================
# 日志
# ============================================================
def get_logger(name, log_dir, fresh=False):
    """
    Args:
        name: logger 名 (会作为日志文件前缀)
        log_dir: 日志目录
        fresh: True 时清空旧的 .info.log; False (默认) 保持原 append 行为
    """
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'{name}.info.log')
    if fresh and os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:  # 避免重复 handler (例如重复调 main)
        return logger

    fmt = logging.Formatter('%(asctime)s - %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    fh = logging.handlers.TimedRotatingFileHandler(
        log_path, when='D', encoding='utf-8')
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def log_config_info(config, logger):
    logger.info('#----------Config info----------#')
    for k, v in config.__dict__.items():
        if k.startswith('_'):
            continue
        # criterion 等 nn.Module 字段 repr 太长, 替换成简称
        if isinstance(v, nn.Module):
            logger.info(f'{k}: <{type(v).__name__}>')
        else:
            try:
                logger.info(f'{k}: {v}')
            except Exception:
                logger.info(f'{k}: <unprintable>')


# ============================================================
# 优化器 / 调度器
# ============================================================
def get_optimizer(config, model):
    if config.opt == 'Adam':
        return torch.optim.Adam(
            model.parameters(), lr=config.lr, betas=config.betas,
            eps=config.eps, weight_decay=config.weight_decay)
    if config.opt == 'AdamW':
        return torch.optim.AdamW(
            model.parameters(), lr=config.lr, betas=config.betas,
            eps=config.eps, weight_decay=config.weight_decay)
    if config.opt == 'SGD':
        return torch.optim.SGD(
            model.parameters(), lr=config.lr,
            momentum=getattr(config, 'momentum', 0.9),
            weight_decay=config.weight_decay)
    return torch.optim.AdamW(
        model.parameters(), lr=config.lr, weight_decay=config.weight_decay)


def get_scheduler(config, optimizer):
    if config.sch == 'CosineAnnealingLR':
        return torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=config.T_max, eta_min=config.eta_min)
    if config.sch == 'StepLR':
        return torch.optim.lr_scheduler.StepLR(
            optimizer, step_size=config.step_size, gamma=config.gamma)
    if config.sch == 'CosineAnnealingWarmRestarts':
        return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer, T_0=config.T_max, eta_min=config.eta_min)
    if config.sch == 'WP_CosineLR':
        wu = getattr(config, 'warm_up_epochs', 5)
        def lr_lambda(epoch):
            if epoch <= wu:
                return (epoch + 1) / max(wu, 1)
            return 0.5 * (math.cos((epoch - wu) / max(config.epochs - wu, 1) * math.pi) + 1)
        return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lr_lambda)
    return torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=config.T_max, eta_min=config.eta_min)


# ============================================================
# 参数量统计
# ============================================================
def cal_params_flops(model, size, logger):
    total = sum(p.numel() for p in model.parameters())
    msg = f'Total params: {total / 1e6:.3f} M'
    print(msg)
    logger.info(msg)
    try:
        from thop import profile
        device = next(model.parameters()).device
        input_tensor = torch.randn(1, 3, size, size).to(device)
        flops, _ = profile(model, inputs=(input_tensor,), verbose=False)
        msg = f'FLOPs: {flops / 1e9:.3f} G  (input {size}x{size})'
        print(msg)
        logger.info(msg)
    except ImportError:
        logger.info('thop 未安装, 跳过 FLOPs 统计')
    except Exception as e:
        logger.info(f'FLOPs 统计跳过: {e}')


# ============================================================
# 可视化
# ============================================================
def _denorm(img_np):
    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])
    return np.clip(img_np * std + mean, 0, 1)


def save_seg_imgs(img, msk, msk_pred, i, save_path, test_data_name=None):
    """
    保存分割可视化: 原图 | GT | 预测
    Args:
        img:      (B, 3, H, W) 输入图像 tensor (已归一化)
        msk:      (B, H, W) GT tensor
        msk_pred: (B, H, W) 预测 tensor
    """
    os.makedirs(save_path, exist_ok=True)

    img_np = img[0].permute(1, 2, 0).detach().cpu().numpy()
    img_np = _denorm(img_np)
    msk_np = msk[0].detach().cpu().numpy().astype(np.int64)
    pred_np = msk_pred[0].detach().cpu().numpy().astype(np.int64)

    msk_color = mask_to_color(msk_np)
    pred_color = mask_to_color(pred_np)

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    axes[0].imshow(img_np)
    axes[0].set_title('Input')
    axes[0].axis('off')
    axes[1].imshow(msk_color)
    axes[1].set_title('Ground Truth')
    axes[1].axis('off')
    axes[2].imshow(pred_color)
    axes[2].set_title('Prediction')
    axes[2].axis('off')
    plt.tight_layout()

    prefix = f'{test_data_name}_' if test_data_name else ''
    plt.savefig(os.path.join(save_path, f'{prefix}{i}.png'),
                dpi=120, bbox_inches='tight')
    plt.close()


def save_color_legend(save_path):
    """保存类别颜色图例"""
    os.makedirs(save_path, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    for cls_id in range(NUM_CLASSES):
        color = np.array(CLASS_COLORS[cls_id]) / 255.0
        name = CLASS_NAMES[cls_id]
        ax.barh(cls_id, 1, color=color, edgecolor='black', linewidth=0.5)
        text_color = 'white' if sum(CLASS_COLORS[cls_id]) < 400 else 'black'
        ax.text(0.5, cls_id, f'{cls_id}: {name}', va='center', ha='center',
                fontsize=10, fontweight='bold', color=text_color)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.5, NUM_CLASSES - 0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title('Class Color Legend')
    ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(os.path.join(save_path, 'color_legend.png'),
                dpi=120, bbox_inches='tight')
    plt.close()
