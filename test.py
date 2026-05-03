"""
test.py  (重写版 - 全分辨率评估)
==================================================
为什么重写:
  原版 test.py 的评估流程是
    Image (任意尺寸)  → resize 到 384×384 → 模型 → argmax → 比较 384×384 GT
  这对裂缝是双重损失:
    (1) GT 在 NEAREST resize 后, 1-3 px 细裂缝大量消失
    (2) 即使预测正确, 也只在 384×384 网格上计分

  这个版本:
    - 在原始分辨率上跑滑窗推理 (与 inference.py 一致)
    - 对每张图分别累加 confusion matrix (StreamingIoU, 不爆内存)
    - 输出标准 mIoU + Tolerance IoU + Boundary F1 + 裂缝 P/R/F1
    - 把每张图的指标写入 CSV, 便于排查最差 N 张
    - 可选 --save_worst_n 保存最差 N 张的可视化

用法:
    # 基本: 全分辨率 + 滑窗 + 全套指标
    python test.py --weights best.pth --data_path /path/to/data

    # 加 TTA
    python test.py --weights best.pth --data_path /path/to/data --tta

    # 多尺度 TTA (慢一些, 更准)
    python test.py --weights best.pth --data_path /path/to/data \
                   --multi_scale 0.75 1.0 1.25

    # 同时保存最差 20 张的可视化
    python test.py --weights best.pth --data_path /path/to/data --save_worst_n 20

    # 兼容老流程: 用 dataset 的 384 fixed-size 评估
    python test.py --weights best.pth --legacy_resize_eval
"""

import os
import csv
import argparse
import warnings
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from tqdm import tqdm

from models.TunnelDefectSeg import TunnelDefectSeg
from util.loader_npy import NpyDataset
from util.metrics import (
    compute_metrics, compute_per_image, compute_tolerance_iou,
    compute_boundary_f1, StreamingIoU, format_metrics_table,
)
from utils import set_seed, get_logger, cal_params_flops, save_color_legend, save_seg_imgs
from configs.config_setting import setting_config
from configs.class_config import NUM_CLASSES, CLASS_NAMES, CLASS_TO_FOLDER
from inference import sliding_window_inference, _normalize_image, _gaussian_weight

warnings.filterwarnings('ignore')


# ============================================================
# 多尺度滑窗推理
# ============================================================
@torch.no_grad()
def multi_scale_predict(model, img_np, device, scales=(1.0,),
                         tile=384, overlap=96, use_tta=False):
    """
    在多个尺度上做滑窗推理, 平均概率后 argmax.
    img_np: (H, W, 3) uint8
    return: (H, W) int64
    """
    import torch.nn.functional as F
    H, W = img_np.shape[:2]
    avg_probs = None
    n = 0

    for scale in scales:
        if abs(scale - 1.0) < 1e-6:
            scaled_img = img_np
        else:
            new_H = max(tile, int(round(H * scale)))
            new_W = max(tile, int(round(W * scale)))
            scaled_img = np.array(
                Image.fromarray(img_np).resize((new_W, new_H), Image.BILINEAR)
            )

        probs = _sliding_window_probs(model, scaled_img, device,
                                       tile=tile, overlap=overlap,
                                       use_tta=use_tta)  # (C, h, w)
        if probs.shape[1:] != (H, W):
            t = torch.from_numpy(probs).unsqueeze(0)
            t = F.interpolate(t, size=(H, W), mode='bilinear', align_corners=False)
            probs = t.squeeze(0).numpy()

        if avg_probs is None:
            avg_probs = probs
        else:
            avg_probs = avg_probs + probs
        n += 1

    avg_probs = avg_probs / max(n, 1)
    return avg_probs.argmax(axis=0).astype(np.int64)


# ============================================================
# 带返回 prob 的滑窗 (multi_scale_predict 内部用)
# 与 inference.sliding_window_inference 同结构, 只是返回 (C, H, W) 概率
# ============================================================
@torch.no_grad()
def _sliding_window_probs(model, img_np, device, tile=384, overlap=96,
                          use_tta=False, num_classes=NUM_CLASSES):
    import torch.nn.functional as F
    H, W = img_np.shape[:2]
    stride = max(1, tile - overlap)
    n_h = max(1, (H - overlap + stride - 1) // stride)
    n_w = max(1, (W - overlap + stride - 1) // stride)
    pad_H = max(0, (n_h - 1) * stride + tile - H)
    pad_W = max(0, (n_w - 1) * stride + tile - W)
    img_pad = np.pad(img_np, ((0, pad_H), (0, pad_W), (0, 0)), mode='reflect')
    Hp, Wp = img_pad.shape[:2]

    prob_sum = torch.zeros((num_classes, Hp, Wp), dtype=torch.float32)
    weight_sum = torch.zeros((1, Hp, Wp), dtype=torch.float32)
    g_w = _gaussian_weight(tile)
    img_tensor = _normalize_image(img_pad).to(device)

    for iy in range(n_h):
        for ix in range(n_w):
            y0 = iy * stride
            x0 = ix * stride
            tile_t = img_tensor[:, y0:y0 + tile, x0:x0 + tile].unsqueeze(0)

            logits = model(tile_t)
            if isinstance(logits, (tuple, list)):
                logits = logits[0]
            probs = F.softmax(logits, dim=1)

            if use_tta:
                lh = model(tile_t.flip(-1))
                if isinstance(lh, (tuple, list)):
                    lh = lh[0]
                probs = probs + F.softmax(lh, dim=1).flip(-1)
                lv = model(tile_t.flip(-2))
                if isinstance(lv, (tuple, list)):
                    lv = lv[0]
                probs = probs + F.softmax(lv, dim=1).flip(-2)
                probs = probs / 3.0

            probs = probs.squeeze(0).cpu()
            probs_w = probs * g_w.unsqueeze(0)
            prob_sum[:, y0:y0 + tile, x0:x0 + tile] += probs_w
            weight_sum[:, y0:y0 + tile, x0:x0 + tile] += g_w.unsqueeze(0)

    prob_sum = prob_sum[:, :H, :W]
    weight_sum = weight_sum[:, :H, :W].clamp_min(1e-6)
    return (prob_sum / weight_sum).numpy()  # (C, H, W) softmax probs


# ============================================================
# 加载单张样本到原始分辨率
# ============================================================
def _load_sample_full_res(sample):
    """加载单张样本到原始分辨率 (兼容文件模式和 .npy 模式)"""
    # .npy 模式: sample 含 _npy_images / _npy_masks 引用
    if '_npy_images' in sample:
        idx = sample['index']
        img = sample['_npy_images'][idx].astype(np.uint8)  # (H, W, 3)
        mask_raw = sample['_npy_masks'][idx]                # (H, W)
        gt = mask_raw.astype(np.int64)
        return img, gt

    # 文件模式 (原版)
    img = np.array(Image.open(sample['img_path']).convert('RGB'))
    mask_bin = np.array(Image.open(sample['mask_path']).convert('L')) > 127
    H, W = img.shape[:2]
    if mask_bin.shape != (H, W):
        # mask 与图像尺寸不一致 (少见), 把 mask 拉伸到图像大小
        mask_bin = np.array(
            Image.fromarray(mask_bin.astype(np.uint8) * 255)
                 .resize((W, H), Image.NEAREST)
        ) > 127
    gt = np.zeros(mask_bin.shape, dtype=np.int64)
    gt[mask_bin] = sample['class_id']
    return img, gt


# ============================================================
# 主测试函数 (全分辨率 + 滑窗)
# ============================================================
def test_full_resolution(model, samples, device, logger, config,
                          tile=384, overlap=96, scales=(1.0,),
                          use_tta=False, save_dir=None,
                          save_worst_n=0, csv_path=None,
                          tolerance=2, theta=2):
    """
    Args:
        samples:    list of {'img_path', 'mask_path', 'class_id', 'folder'}
        scales:     单尺度 (1.0,) 或多尺度 (0.75, 1.0, 1.25)
        save_worst_n: 保存最差 N 张的可视化, 0 = 不保存
        csv_path:   每张图的指标 CSV 路径
    """
    streaming = StreamingIoU(NUM_CLASSES)
    per_image_records = []

    # 多尺度时预先打印一次
    scales = list(scales)
    if len(scales) > 1:
        logger.info(f'  多尺度 TTA: {scales}')

    for sample in tqdm(samples, desc='Test (full-res)'):
        img, gt = _load_sample_full_res(sample)
        H, W = img.shape[:2]

        if len(scales) == 1 and abs(scales[0] - 1.0) < 1e-6:
            pred = sliding_window_inference(model, img, device,
                                             tile=tile, overlap=overlap,
                                             use_tta=use_tta)
        else:
            pred = multi_scale_predict(model, img, device, scales=scales,
                                        tile=tile, overlap=overlap,
                                        use_tta=use_tta)

        # 累加全局指标
        streaming.update(pred, gt)

        # 计算每张图指标 (用于 CSV / worst-N)
        m = compute_per_image(pred, gt, num_classes=NUM_CLASSES,
                               tolerance=tolerance, theta=theta,
                               crack_class=1)
        rec = {
            'img_path': sample['img_path'],
            'class_folder': sample['folder'],
            'gt_class_id': sample['class_id'],
            'H': H, 'W': W,
            **{k: v for k, v in m.items()
               if k in ('mIoU', 'pixel_acc',
                        'crack_tol_iou', 'crack_bf1',
                        'crack_precision', 'crack_recall', 'crack_f1')}
        }
        # 加入每类 IoU
        for c in range(NUM_CLASSES):
            rec[f'IoU_c{c}'] = m.get(f'IoU_class{c}', float('nan'))
        per_image_records.append((rec, pred, gt, img, sample))

    # ----- 全局指标 (StreamingIoU 聚合) -----
    global_metrics = streaming.compute()

    # 全分辨率 Tolerance IoU / Boundary F1 用 per-image 取均值更合理
    # (因为它们在像素总和上不可加, 必须 per-image 算)
    tol_ious_crack = [r[0]['crack_tol_iou'] for r in per_image_records
                       if not np.isnan(r[0]['crack_tol_iou'])]
    bf1s_crack = [r[0]['crack_bf1'] for r in per_image_records
                   if not np.isnan(r[0]['crack_bf1'])]
    if tol_ious_crack:
        global_metrics['crack_tol_iou'] = float(np.mean(tol_ious_crack))
    if bf1s_crack:
        global_metrics['crack_bf1'] = float(np.mean(bf1s_crack))

    crack_pix_f1s = [r[0]['crack_f1'] for r in per_image_records
                      if not np.isnan(r[0]['crack_f1'])]
    if crack_pix_f1s:
        global_metrics['crack_f1'] = float(np.mean(crack_pix_f1s))
        global_metrics['crack_precision'] = float(np.mean([
            r[0]['crack_precision'] for r in per_image_records]))
        global_metrics['crack_recall'] = float(np.mean([
            r[0]['crack_recall'] for r in per_image_records]))

    # ----- Logger 报告 -----
    logger.info('#----- 全分辨率测试结果 -----#')
    logger.info(format_metrics_table(global_metrics, class_names=CLASS_NAMES))

    # ----- CSV -----
    if csv_path:
        keys = list(per_image_records[0][0].keys()) if per_image_records else []
        with open(csv_path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for rec, *_ in per_image_records:
                w.writerow(rec)
        logger.info(f'  per-image CSV: {csv_path}')

    # ----- 最差 N 张可视化 -----
    if save_worst_n > 0 and save_dir is not None:
        # 按 mIoU 升序
        ranked = sorted(per_image_records, key=lambda r: r[0]['mIoU'])
        os.makedirs(save_dir, exist_ok=True)
        for i, (rec, pred, gt, img, sample) in enumerate(ranked[:save_worst_n]):
            base = os.path.splitext(os.path.basename(sample['img_path']))[0]
            out = os.path.join(save_dir,
                                f'worst_{i:03d}_{rec["class_folder"]}_{base}_'
                                f'mIoU{rec["mIoU"]:.3f}.png')
            _save_triple_panel(img, gt, pred, out, rec)
        logger.info(f'  最差 {save_worst_n} 张可视化: {save_dir}')

    return global_metrics


def _save_triple_panel(img, gt, pred, out_path, rec):
    """保存 3 联图: input | gt | pred, 标题写指标"""
    from matplotlib import pyplot as plt
    from configs.class_config import mask_to_color
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    axes[0].imshow(img); axes[0].set_title('Input'); axes[0].axis('off')
    axes[1].imshow(mask_to_color(gt)); axes[1].set_title('GT'); axes[1].axis('off')
    axes[2].imshow(mask_to_color(pred)); axes[2].set_title('Pred'); axes[2].axis('off')
    title = (f'mIoU={rec["mIoU"]:.3f}  crack_tol_iou={rec.get("crack_tol_iou",0):.3f}  '
             f'crack_F1={rec.get("crack_f1",0):.3f}')
    fig.suptitle(title, fontsize=11, y=0.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=110, bbox_inches='tight')
    plt.close()


# ============================================================
# 老的 384-resize 评估流程 (兼容)
# ============================================================
def test_legacy_resize(model, test_loader, device, logger, config):
    """与原 engine.test_one_epoch 等价, 用于和新流程对比"""
    from util.engine import test_one_epoch
    from torch.utils.data import DataLoader  # noqa
    return test_one_epoch(test_loader, model, config.criterion, logger, config)


# ============================================================
# CLI
# ============================================================
def parse_args():
    ap = argparse.ArgumentParser('TunnelDefectSeg 测试 (全分辨率 + 多指标)')
    ap.add_argument('--weights', type=str, required=True)
    ap.add_argument('--data_path', type=str, default=None)
    ap.add_argument('--gpu', type=str, default='0')

    # 滑窗参数
    ap.add_argument('--tile', type=int, default=384)
    ap.add_argument('--overlap', type=int, default=96)

    # TTA
    ap.add_argument('--tta', action='store_true', help='HFlip + VFlip')
    ap.add_argument('--multi_scale', type=float, nargs='+', default=None,
                    help='多尺度比例, 如 0.75 1.0 1.25')

    # 评估参数
    ap.add_argument('--tolerance', type=int, default=2,
                    help='Tolerance IoU 容差像素')
    ap.add_argument('--theta', type=int, default=2,
                    help='Boundary F1 容差像素')

    # 输出
    ap.add_argument('--out_dir', type=str, default=None,
                    help='覆盖 config.work_dir')
    ap.add_argument('--save_worst_n', type=int, default=0,
                    help='保存最差 N 张的可视化, 用于排查')

    # 兼容老流程
    ap.add_argument('--legacy_resize_eval', action='store_true',
                    help='与原 test.py 一致: 384 fixed-size resize 评估')
    return ap.parse_args()


def main():
    args = parse_args()
    config = setting_config

    if args.data_path:
        config.data_path = args.data_path
    if args.out_dir:
        config.work_dir = args.out_dir

    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu

    log_dir = os.path.join(config.work_dir, 'log')
    out_dir = os.path.join(config.work_dir, 'outputs')
    worst_dir = os.path.join(out_dir, 'worst_cases')
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    logger = get_logger('test', log_dir)
    save_color_legend(out_dir)

    set_seed(config.seed)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # ----- 模型 -----
    logger.info('#--------- 加载模型 ---------#')
    model = TunnelDefectSeg(**config.model_config).to(device)
    cal_params_flops(model, args.tile, logger)

    state = torch.load(args.weights, map_location='cpu')
    if isinstance(state, dict) and 'model_state_dict' in state:
        state = state['model_state_dict']
    model.load_state_dict(state, strict=False)
    model.eval()
    logger.info(f'  权重: {args.weights}')

    # ----- 数据集 -----
    npy_dir = getattr(config, 'npy_dir', os.path.join(config.data_path, 'npy'))
    test_set = NpyDataset(npy_dir, split='test',
                           img_size=(args.tile, args.tile), augment=False)
    logger.info(f'  test 样本数: {len(test_set)}')

    if args.legacy_resize_eval:
        # 老流程
        from torch.utils.data import DataLoader
        loader = DataLoader(test_set, batch_size=1, shuffle=False,
                             num_workers=config.num_workers, pin_memory=True)
        logger.info('#--------- 老流程: 384 resize 评估 ---------#')
        test_legacy_resize(model, loader, device, logger, config)
        return

    # ----- 全分辨率评估 -----
    scales = tuple(args.multi_scale) if args.multi_scale else (1.0,)
    csv_path = os.path.join(out_dir, 'per_image_metrics.csv')

    logger.info('#--------- 全分辨率评估 (滑窗) ---------#')
    logger.info(f'  tile={args.tile} overlap={args.overlap} '
                f'tta={args.tta} scales={scales}')
    logger.info(f'  Tolerance IoU 容差={args.tolerance} px, '
                f'Boundary F1 θ={args.theta} px')

    test_full_resolution(
        model, test_set.samples, device, logger, config,
        tile=args.tile, overlap=args.overlap,
        scales=scales, use_tta=args.tta,
        save_dir=worst_dir, save_worst_n=args.save_worst_n,
        csv_path=csv_path,
        tolerance=args.tolerance, theta=args.theta,
    )
    logger.info('#--------- 测试完成 ---------#')
    logger.info(f'  日志:  {log_dir}/test.info.log')
    logger.info(f'  指标:  {csv_path}')
    if args.save_worst_n > 0:
        logger.info(f'  失败 case: {worst_dir}')


if __name__ == '__main__':
    main()
