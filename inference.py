"""
inference.py  (优化版 — 滑窗 + TTA)
==================================================
改动重点:
  * 滑窗推理 (sliding window with overlap)
    - 默认 tile=384, overlap=96 (overlap 比例 25%)
    - 边缘高斯加权融合 → 拼接处不出现 grid artifacts
    - 大图直接保留原分辨率, 不再 384 resize → argmax → 上采样
    这是裂缝 IoU 涨点的最大来源 (1-3 px 的细裂缝在 resize 后基本消失)

  * 可选 TTA (HFlip + VFlip)
    - 与滑窗组合时, 总成本 4× 单次前向, 一般可接受
    - 关:  python inference.py --weights ...
    - 开:  python inference.py --weights ... --tta

  * 修正默认 c_list 与训练默认对齐 (Small (24,48,96,128,160))
"""

import os
import glob
import argparse
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from matplotlib import pyplot as plt

from models.TunnelDefectSeg import TunnelDefectSeg
from configs.class_config import NUM_CLASSES, CLASS_NAMES, mask_to_color


IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMG_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ============================================================
# 高斯权重窗 — 滑窗融合时给 tile 中心高权重, 边缘较低
# 避免拼接缝
# sigma_scale=0.25 时 center/edge ≈ 7:1, 边缘 ~14% 权重 (够稳定, 又能压住边缘
# 不可靠的预测; 0.125 时边缘 ~0% 会让仅被一个 tile 覆盖的边角像素在归一化后
# 数值不稳)
# ============================================================
def _gaussian_weight(tile, sigma_scale=0.25):
    """生成 (tile, tile) 高斯权重, 中心 ≈1, 边缘 ≈0.14"""
    sigma = tile * sigma_scale
    coords = np.arange(tile, dtype=np.float32) - (tile - 1) / 2
    g1d = np.exp(-(coords ** 2) / (2 * sigma ** 2))
    g2d = np.outer(g1d, g1d)
    return torch.from_numpy(g2d).float()


def _normalize_image(img_np):
    """uint8 RGB (H, W, 3) → float32 (3, H, W) ImageNet-normalized"""
    arr = img_np.astype(np.float32) / 255.0
    arr = (arr - IMG_MEAN) / IMG_STD
    return torch.from_numpy(arr).permute(2, 0, 1).float()


# ============================================================
# 滑窗推理核心
# ============================================================
@torch.no_grad()
def sliding_window_inference(model, img_np, device, tile=384, overlap=96,
                             use_tta=False, num_classes=NUM_CLASSES):
    """
    img_np: (H, W, 3) uint8
    return: (H, W) int64  argmax 类别

    工作流:
      1. 按 stride = tile - overlap 步进切 tile
      2. 不足一个 tile 的边缘 → reflect padding 后再切
      3. tile 前向 (可选 TTA), softmax 后乘高斯权重累加到全图
      4. 全图按权重归一, argmax
    """
    H, W = img_np.shape[:2]
    stride = max(1, tile - overlap)

    # 计算实际切片数
    n_h = max(1, (H - overlap + stride - 1) // stride)
    n_w = max(1, (W - overlap + stride - 1) // stride)
    pad_H = max(0, (n_h - 1) * stride + tile - H)
    pad_W = max(0, (n_w - 1) * stride + tile - W)

    # reflect padding 减少边界为 0 的伪影
    img_pad = np.pad(img_np, ((0, pad_H), (0, pad_W), (0, 0)), mode='reflect')
    Hp, Wp = img_pad.shape[:2]

    # 累加 buffer
    prob_sum = torch.zeros((num_classes, Hp, Wp), dtype=torch.float32)
    weight_sum = torch.zeros((1, Hp, Wp), dtype=torch.float32)
    g_w = _gaussian_weight(tile)  # (tile, tile)

    img_tensor = _normalize_image(img_pad).to(device)  # (3, Hp, Wp)

    for iy in range(n_h):
        for ix in range(n_w):
            y0 = iy * stride
            x0 = ix * stride
            tile_t = img_tensor[:, y0:y0 + tile, x0:x0 + tile].unsqueeze(0)  # (1,3,t,t)

            probs = _forward_once(model, tile_t, use_tta)  # (1, C, t, t)
            probs = probs.squeeze(0).cpu()                  # (C, t, t)

            # 应用高斯权重
            probs_w = probs * g_w.unsqueeze(0)
            prob_sum[:, y0:y0 + tile, x0:x0 + tile] += probs_w
            weight_sum[:, y0:y0 + tile, x0:x0 + tile] += g_w.unsqueeze(0)

    # 去 padding + 归一化
    prob_sum = prob_sum[:, :H, :W]
    weight_sum = weight_sum[:, :H, :W].clamp_min(1e-6)
    avg_probs = prob_sum / weight_sum
    pred = avg_probs.argmax(dim=0).numpy().astype(np.int64)
    return pred


def _forward_once(model, tile_t, use_tta):
    """前向 (可选 HFlip + VFlip TTA), 返回 softmax 后的概率"""
    logits = model(tile_t)
    if isinstance(logits, (tuple, list)):
        logits = logits[0]
    probs = F.softmax(logits, dim=1)

    if use_tta:
        # H-flip
        lh = model(tile_t.flip(-1))
        if isinstance(lh, (tuple, list)):
            lh = lh[0]
        probs = probs + F.softmax(lh, dim=1).flip(-1)
        # V-flip
        lv = model(tile_t.flip(-2))
        if isinstance(lv, (tuple, list)):
            lv = lv[0]
        probs = probs + F.softmax(lv, dim=1).flip(-2)
        probs = probs / 3.0
    return probs


# ============================================================
# 单图全流程: 加载 → 推理 → 出图
# ============================================================
def inference_single(model, img_path, device, tile=384, overlap=96,
                      use_tta=False, scales=None):
    """
    Args:
        scales: None 或 (1.0,) 单尺度; 多尺度如 (0.75, 1.0, 1.25)
    """
    img = np.array(Image.open(img_path).convert('RGB'))
    if scales and len(scales) > 1:
        # 复用 test.py 的 multi_scale_predict (避免重复代码)
        try:
            from test import multi_scale_predict
            pred = multi_scale_predict(model, img, device,
                                        scales=scales, tile=tile,
                                        overlap=overlap, use_tta=use_tta)
        except ImportError:
            # test.py 不可用 (例如打包推理时), 退化为单尺度
            pred = sliding_window_inference(model, img, device,
                                             tile=tile, overlap=overlap,
                                             use_tta=use_tta)
    else:
        pred = sliding_window_inference(model, img, device,
                                         tile=tile, overlap=overlap,
                                         use_tta=use_tta)
    return pred, (img.shape[1], img.shape[0])  # (W, H)


def create_overlay(img_path, pred_mask, alpha=0.5):
    img = np.array(Image.open(img_path).convert('RGB'))
    if img.shape[:2] != pred_mask.shape:
        img = np.array(Image.fromarray(img).resize(
            (pred_mask.shape[1], pred_mask.shape[0]), Image.BILINEAR))
    color_mask = mask_to_color(pred_mask)
    overlay = img.copy()
    fg = pred_mask > 0
    overlay[fg] = (img[fg] * (1 - alpha) + color_mask[fg] * alpha).astype(np.uint8)
    return overlay


def save_result(img_path, pred_mask, output_path):
    img = np.array(Image.open(img_path).convert('RGB'))
    if img.shape[:2] != pred_mask.shape:
        img = np.array(Image.fromarray(img).resize(
            (pred_mask.shape[1], pred_mask.shape[0]), Image.BILINEAR))
    color_mask = mask_to_color(pred_mask)
    overlay = create_overlay(img_path, pred_mask, alpha=0.5)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    axes[0].imshow(img);        axes[0].set_title('Input');      axes[0].axis('off')
    axes[1].imshow(color_mask); axes[1].set_title('Prediction'); axes[1].axis('off')
    axes[2].imshow(overlay);    axes[2].set_title('Overlay');    axes[2].axis('off')

    detected = [CLASS_NAMES[c] for c in np.unique(pred_mask) if c > 0]
    fig.suptitle('Detected: ' + (', '.join(detected) if detected else 'None'),
                 fontsize=12, y=0.02)
    plt.tight_layout()
    plt.savefig(output_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f'  saved: {output_path}'
          + (f'  ({", ".join(detected)})' if detected else ''))


# ============================================================
# 主入口
# ============================================================
def main():
    ap = argparse.ArgumentParser('TunnelDefectSeg 推理 (滑窗 + TTA)')
    ap.add_argument('--weights', type=str, required=True)
    ap.add_argument('--image', type=str, default=None)
    ap.add_argument('--image_dir', type=str, default=None)
    ap.add_argument('--output', type=str, default='result.png')
    ap.add_argument('--output_dir', type=str, default='inference_results/')
    ap.add_argument('--tile', type=int, default=384,
                    help='滑窗大小, 应等于训练时的 input_size_h')
    ap.add_argument('--overlap', type=int, default=96,
                    help='滑窗重叠像素, 推荐 tile 的 25%')
    ap.add_argument('--tta', action='store_true',
                    help='HFlip+VFlip TTA, 提升 ~0.5-1.5 IoU')
    ap.add_argument('--multi_scale', type=float, nargs='+', default=None,
                    help='多尺度滑窗融合, 如 0.75 1.0 1.25; 与 --tta 可叠加')
    ap.add_argument('--c_list', type=str, default='24,48,96,128,160',
                    help='与训练时相同的通道配置')
    ap.add_argument('--aspp_ch', type=int, default=128)
    ap.add_argument('--gpu', type=str, default='0')
    args = ap.parse_args()

    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    c_list = tuple(int(x) for x in args.c_list.split(','))
    model = TunnelDefectSeg(num_classes=NUM_CLASSES,
                             c_list=c_list,
                             aspp_ch=args.aspp_ch).to(device)
    state = torch.load(args.weights, map_location='cpu')
    if isinstance(state, dict) and 'model_state_dict' in state:
        state = state['model_state_dict']
    model.load_state_dict(state, strict=False)
    model.eval()
    print(f'模型加载完成 (c_list={c_list}, aspp_ch={args.aspp_ch})')
    print(f'滑窗: tile={args.tile} overlap={args.overlap} '
          f'tta={args.tta} multi_scale={args.multi_scale}')

    scales = tuple(args.multi_scale) if args.multi_scale else None

    if args.image:
        pred, _ = inference_single(model, args.image, device,
                                    tile=args.tile, overlap=args.overlap,
                                    use_tta=args.tta, scales=scales)
        save_result(args.image, pred, args.output)
    elif args.image_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        files = []
        for ext in ('*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff'):
            files.extend(glob.glob(os.path.join(args.image_dir, ext)))
        files = sorted(files)
        print(f'批量推理: {len(files)} 张')
        for p in files:
            base = os.path.splitext(os.path.basename(p))[0]
            out = os.path.join(args.output_dir, f'{base}_result.png')
            pred, _ = inference_single(model, p, device,
                                        tile=args.tile, overlap=args.overlap,
                                        use_tta=args.tta, scales=scales)
            save_result(p, pred, out)
        print(f'完成, 结果在: {args.output_dir}')
    else:
        print('请指定 --image 或 --image_dir')


if __name__ == '__main__':
    main()
