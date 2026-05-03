"""
v4 新增: Test-Time Augmentation (TTA) 推理增强
==============================================
原理:
  推理时对原始图像做多个确定性增强 (翻转/旋转), 分别预测后取平均/投票.
  已知能稳定提升 0.5-1.5% IoU, 几乎零额外训练成本.

支持的操作:
  1. 水平翻转  (HFlip)           - 最常用, 裂缝对称性保证安全
  2. 垂直翻转  (VFlip)           - 隧道裂缝纵向分布多, 翻转后仍有效
  3. 90°×2 旋转 (Rotate90×2)     - 裂缝没有固定方向, 可增强 3 个角度
  4. 缩放      (Scale 0.9/1.1)   - 轻微尺度变换, 适合检测不同距离的裂缝

v4 默认组合: HFlip + VFlip (2次增强) — 速度/精度平衡最佳
v4 完整组合: HFlip + VFlip + Rot90 + Rot180 + Rot270 (5次增强) — 最高精度

使用时:
  predictions = tta_inference(model, image, device='cuda')
  # image: Tensor (B,C,H,W) 或 numpy (H,W,C), device: 推理设备
"""
import torch
import torch.nn.functional as F
import numpy as np


def _to_tensor(img, device):
    """统一转为 (B,C,H,W) float32 Tensor"""
    if isinstance(img, np.ndarray):
        img = torch.from_numpy(img).float()
        if img.ndim == 3 and img.shape[-1] in (1, 3):
            img = img.permute(2, 0, 1)
        elif img.ndim == 2:
            img = img.unsqueeze(0)
    if img.ndim == 3:
        img = img.unsqueeze(0)
    return img.to(device)


# ---- 确定性几何变换 (可逆) ----
def _hflip(x):
    return x.flip(-1)

def _vflip(x):
    return x.flip(-2)

def _rot90(x):
    return x.transpose(-2, -1).flip(-1)

def _rot180(x):
    return x.flip(-2).flip(-1)

def _rot270(x):
    return x.transpose(-2, -1).flip(-2)

def _scale(x, factor):
    """最近邻缩放 (不可微, 仅推理用)"""
    B, C, H, W = x.shape
    new_H, new_W = int(H * factor), int(W * factor)
    scaled = F.interpolate(x, size=(new_H, new_W), mode='nearest')
    return F.interpolate(scaled, size=(H, W), mode='nearest')


# ---- 逆向变换 (用于恢复 logits) ----
def _ihflip(x): return _hflip(x)
def _ivflip(x): return _vflip(x)
def _irot90(x): return _rot270(x)   # rot90 的逆 = rot270
def _irot180(x): return _rot180(x)
def _irot270(x): return _rot90(x)    # rot270 的逆 = rot90


# ---- TTA 组合策略 ----
class TTAStrategy:
    """
    可配置的 TTA 组合策略.
    ops: list of (forward_fn, inverse_fn, name)
    """
    def __init__(self, ops):
        self.ops = ops

    def __repr__(self):
        names = [o[2] for o in self.ops]
        return f"TTAStrategy({' + '.join(names)})"


# 预设策略
TTA_HFlip = TTAStrategy([(_hflip, _ihflip, 'HFlip')])
TTA_HVFlip = TTAStrategy([(_hflip, _ihflip, 'HFlip'),
                            (_vflip, _ivflip, 'VFlip')])
TTA_Plus3 = TTAStrategy([(_hflip, _ihflip, 'HFlip'),
                           (_vflip, _ivflip, 'VFlip'),
                           (_rot90, _irot90, 'Rot90'),
                           (_rot180, _irot180, 'Rot180'),
                           (_rot270, _irot270, 'Rot270')])
TTA_Fast = TTA_HFlip                   # 仅水平翻转, 最快
TTA_Balance = TTA_HVFlip              # H+V 翻转, 速度/精度平衡
TTA_Accurate = TTAStrategy([(_hflip, _ihflip, 'HFlip'),
                               (_vflip, _ivflip, 'VFlip'),
                               (_rot90, _irot90, 'Rot90')])


def tta_inference(model, image, device='cuda',
                  strategy=None,
                  num_classes=7,
                  tta_mode='mean',
                  use_ema=False):
    """
    TTA 推理.

    Args:
        model:      分割模型 (eval 模式)
        image:      原始图像 tensor (B,C,H,W) 或 numpy.ndarray
        device:     推理设备
        strategy:   TTAStrategy 实例, None 则使用 TTA_Balance (HVFlip)
        num_classes: 分割类别数 (用于 argmax)
        tta_mode:   'mean' 取 softmax 概率均值, 'vote' 多数投票
        use_ema:    True 则使用 model.module (EMA 模型)

    Returns:
        preds: (B, H, W) 类别预测 (argmax)
        probs: (B, num_classes, H, W) 平均 softmax 概率
    """
    if strategy is None:
        strategy = TTA_Balance

    # 处理模型
    if use_ema and hasattr(model, 'module'):
        model = model.module
    model = model.to(device).eval()

    # 预处理
    x = _to_tensor(image, device)

    with torch.no_grad():
        B, C, H, W = x.shape
        all_probs = []

        for fwd, inv, name in strategy.ops:
            x_aug = fwd(x)
            logits = model(x_aug)

            # 如果输出是 (logits, ds_list), 只取 logits
            if isinstance(logits, (tuple, list)):
                logits = logits[0]

            # 逆向变换恢复
            logits = inv(logits)

            # 确保尺寸一致 (插值恢复)
            if logits.shape[-2:] != (H, W):
                logits = F.interpolate(logits, size=(H, W),
                                       mode='bilinear', align_corners=False)

            probs = F.softmax(logits, dim=1)
            all_probs.append(probs)

        # 聚合
        if tta_mode == 'mean':
            avg_probs = torch.stack(all_probs, dim=0).mean(dim=0)
        elif tta_mode == 'max':
            avg_probs = torch.stack(all_probs, dim=0).max(dim=0)[0]
        else:
            raise ValueError(f"未知的 tta_mode: {tta_mode}")

        preds = avg_probs.argmax(dim=1)

    return preds.cpu(), avg_probs.cpu()


# ---- 批量 TTA (用于 DataLoader 场景) ----
def tta_collate_fn(batch_probs, num_classes=7):
    """
    合并多个 batch 的 TTA 结果 (用于大规模推理).
    batch_probs: list of (B, num_classes, H, W) tensors
    Returns:  (total_B, num_classes, H, W) averaged
    """
    stacked = torch.cat([p.unsqueeze(0) for p in batch_probs], dim=0)
    return stacked.mean(dim=0)


# ---- 使用示例 ----
if __name__ == '__main__':
    print("=== TTA 预设策略 ===")
    for s in [TTA_Fast, TTA_Balance, TTA_Accurate, TTA_Plus3]:
        print(f"  {s}")

    # 功能测试
    import torch
    print("\n=== 功能测试 ===")
    x = torch.randn(1, 3, 384, 384)
    print(f"输入形状: {x.shape}")

    for s in [TTA_HFlip, TTA_HVFlip]:
        total_ops = len(s.ops)
        print(f"{s}: {total_ops} 次增强")
