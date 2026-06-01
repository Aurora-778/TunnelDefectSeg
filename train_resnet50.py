"""
================================================================================
  ResNet50 渗水/裂缝分割 — 统一训练框架
  ======================================
  预训练: C:\\Users\\26822\\Downloads\\resnet50_caffe-788b5fa3.pth
  数据集: C:\\Users\\26822\\Downloads\\data
  类别  : background, simple, blocky, pipeline, vertical, horizontal (num_classes=6)
  输入  : 384×384  |  Epochs: 200  |  Split: 700/150/150
================================================================================
"""

import os, sys, time, math, random, shutil
import numpy as np
from PIL import Image
from glob import glob

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR

import torchvision
from torchvision.models.resnet import resnet50, ResNet50_Weights
from torchvision import transforms

from data_adapter import discover_samples, split_dataset, load_label_mask
from metrics_adapter import StreamingSegMetrics

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================================================
# 全局配置（统一标准化）
# ============================================================
class Config:
    # ---- 路径 ----
    DATA_ROOT   = r"C:\Users\26822\Downloads\data"
    PRETRAINED  = r"C:\Users\26822\Downloads\resnet50_caffe-788b5fa3.pth"
    EXP_NAME    = os.environ.get("EXP_NAME", "ResNet50_FCN_6cls")
    SAVE_DIR    = os.path.join(DATA_ROOT, "experiments", EXP_NAME)

    # ---- 类别定义 ----
    NUM_CLASSES = 6
    CLASSES     = ("background", "simple", "blocky", "pipeline", "vertical", "horizontal")
    LABEL_MODE  = "multiclass"  # multiclass or binary
    PALETTE     = [
        [0,   0,   0  ],   # 0  background  – 黑
        [0,   200, 255],   # 1  simple      – 青蓝
        [255, 80,  80 ],   # 2  blocky      – 红
        [140, 90,  255],   # 3  pipeline    – 紫
        [255, 200, 0  ],   # 4  vertical    – 金黄
        [60,  220, 90 ],   # 5  horizontal  – 翠绿
    ]

    # ---- 数据集划分 700/150/150 ----
    TRAIN_N = 700
    VAL_N   = 150
    TEST_N  = 150

    # ---- 训练超参 ----
    INPUT_SIZE  = (384, 384)
    EPOCHS      = 200
    BATCH_SIZE  = 8
    LR          = 1e-3
    WEIGHT_DECAY= 1e-4
    NUM_WORKERS = 0   # Windows 下改为 0 避免 multiprocessing 错误
    AUTO_RESUME = os.environ.get("AUTO_RESUME", "1") != "0"
    CHECKPOINT_EVERY_EPOCHS = int(os.environ.get("CHECKPOINT_EVERY_EPOCHS", "30"))

    # ---- 设备 ----
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # ---- 随机种子 ----
    SEED = 42

def seed_everything(seed):
    random.seed(seed); np.random.seed(seed)
    torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)

seed_everything(Config.SEED)
os.makedirs(Config.SAVE_DIR, exist_ok=True)


# ============================================================
# 1. Dataset
# ============================================================
class CrackDataset(Dataset):
    def __init__(self, pairs, transform=None, mask_transform=None,
                 label_strategy="auto", is_train=True):
        self.pairs   = pairs
        self.transform = transform
        self.mask_transform = mask_transform
        self.label_strategy = label_strategy
        self.is_train = is_train
        self.to_tensor = transforms.ToTensor()

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        img_path, mask_path = self.pairs[idx]
        folder_name = os.path.basename(os.path.dirname(os.path.dirname(img_path)))

        # 读取图像
        img = Image.open(img_path).convert("RGB")

        # 读取 mask
        mask_6cls = load_label_mask(mask_path, folder_name, num_classes=Config.NUM_CLASSES, label_mode=Config.LABEL_MODE)

        # 图像 transforms（含 Resize → 384x384）
        if self.transform:
            img = self.transform(img)
        # 此时 img 已经是 Tensor [3, 384, 384]

        # ---- mask 同步 Resize 到 384x384 ----
        # 用 F.interpolate 最近邻插值，保持类别整数不模糊
        mask_t = torch.from_numpy(mask_6cls).long().unsqueeze(0).unsqueeze(0).float()
        # mask_t: [1, 1, H_original, W_original]
        mask_resized = F.interpolate(
            mask_t, size=Config.INPUT_SIZE, mode="nearest"
        )  # [1, 1, 384, 384]
        mask_tensor = mask_resized.squeeze(0).squeeze(0).long()
        # mask_tensor: [384, 384]

        return img, mask_tensor, img_path


# ============================================================
# 2. 模型：ResNet50 Encoder + FCN / DeepLabV3 分割头
# ============================================================
class ResNet50SegmentationModel(nn.Module):
    """
    使用 torchvision ResNet50 预训练权重 + FCN/DeepLabV3 分割头
    支持加载 Caffe 预训练权重（resnet50_caffe-788b5fa3.pth）
    """

    def __init__(self, num_classes=6, backbone="fcn", pretrained_path=None):
        super().__init__()
        self.num_classes = num_classes
        self.backbone_name = backbone

        # ---- Backbone: ResNet50 ----
        if pretrained_path and os.path.exists(pretrained_path):
            print(f"[模型] 加载 Caffe 预训练: {pretrained_path}")
            self.backbone_raw = resnet50(weights=None)
            raw_state = torch.load(pretrained_path, map_location="cpu", weights_only=True)
            state = raw_state["state_dict"] if isinstance(raw_state, dict) and "state_dict" in raw_state else raw_state
            result = self.backbone_raw.load_state_dict(state, strict=False)
            loaded = len(state) - len(result.missing_keys)
            print(f"[模型] Caffe 权重加载完成，{loaded}/{len(state)} keys 成功匹配"
                  f"（missing {len(result.missing_keys)}，不含 fc 层，正常）")
        else:
            print("[模型] 使用 TorchVision ResNet50 预训练权重")
            self.backbone_raw = resnet50(weights=ResNet50_Weights.DEFAULT)

        # 去掉最后的 avgpool + fc (children[:-2] 保留了 conv1~layer4)
        # layer4 输出: [B, 2048, H/32, W/32] ← FCN 需要的特征图分辨率
        self.backbone = nn.Sequential(
            self.backbone_raw.conv1,
            self.backbone_raw.bn1,
            self.backbone_raw.relu,
            self.backbone_raw.maxpool,
            self.backbone_raw.layer1,
            self.backbone_raw.layer2,
            self.backbone_raw.layer3,
            self.backbone_raw.layer4,
        )
        self.out_channels = 2048

        # ---- 分割头 ----
        self.seg_head = nn.Sequential(
            nn.Conv2d(self.out_channels, self.out_channels // 4, 3, padding=1),
            nn.BatchNorm2d(self.out_channels // 4),
            nn.ReLU(inplace=True),
            nn.Conv2d(self.out_channels // 4, num_classes, 1),
        )

    def forward(self, x):
        input_size = x.shape[-2:]
        features = self.backbone(x)                     # [B, 2048, H/32, W/32]
        logits = self.seg_head(features)               # [B, num_classes, H/32, W/32]
        out = F.interpolate(logits, size=input_size, mode="bilinear", align_corners=False)
        return out


class DeepLabV3Head(nn.Module):
    """DeepLabV3 分割头（含 ASPP）"""
    def __init__(self, in_channels, num_classes):
        super().__init__()
        self.aspp = ASPP(in_channels, [6, 12, 18])
        self.conv = nn.Sequential(
            nn.Conv2d(self.aspp.out_channels, 256, 3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, num_classes, 1),
        )

    def forward(self, x):
        return self.conv(self.aspp(x))


class ASPP(nn.Module):
    def __init__(self, in_channels, dilations):
        super().__init__()
        self.convs = nn.ModuleList()
        for d in dilations:
            self.convs.append(nn.Sequential(
                nn.Conv2d(in_channels, 256, 3, padding=d, dilation=d),
                nn.BatchNorm2d(256),
                nn.ReLU(inplace=True),
            ))
        self.proj = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(in_channels, 256, 1),
            nn.ReLU(inplace=True),
        )
        self.out_channels = 256 * (len(dilations) + 1)

    def forward(self, x):
        res = [proj(x) for proj in self.proj]  # [B, 256, 1, 1]
        # 自适应池化后需要上采样到相同尺寸
        res[0] = F.interpolate(res[0], size=x.shape[-2:], mode="bilinear", align_corners=False)
        for conv in self.convs:
            res.append(conv(x))
        return torch.cat(res, dim=1)


# ============================================================
# 3. 指标计算
# ============================================================
def compute_confusion_matrix(pred, target, num_classes):
    """计算一个 batch 的混淆矩阵。"""
    if pred.dim() == 4:
        pred = pred.argmax(dim=1)

    pred = pred.flatten().cpu().numpy().astype(np.int64)
    target = target.flatten().cpu().numpy().astype(np.int64)

    valid = (target >= 0) & (target < num_classes)
    idx = num_classes * target[valid] + pred[valid]
    cm = np.bincount(idx, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def metrics_from_confusion_matrix(cm):
    """从累计混淆矩阵计算 pixel acc / mIoU / mDice / per-class 指标。"""
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
    mIou = float(np.nanmean(ious)) if np.any(~np.isnan(ious)) else 0.0
    mDice = float(np.nanmean(dices)) if np.any(~np.isnan(dices)) else 0.0
    return pixel_acc, mIou, mDice, ious.tolist(), dices.tolist()


def class_accuracy_from_confusion_matrix(cm):
    """从累计混淆矩阵计算每类 accuracy 和 mPA。"""
    tp = np.diag(cm).astype(np.float64)
    support = cm.sum(axis=1).astype(np.float64)

    class_accs = np.full(cm.shape[0], np.nan, dtype=np.float64)
    valid = support > 0
    class_accs[valid] = tp[valid] / support[valid]

    mpa = float(np.nanmean(class_accs)) if np.any(~np.isnan(class_accs)) else 0.0
    return mpa, class_accs.tolist()


def compute_model_stats(model, input_size=(1, 3, 384, 384), device="cuda"):
    """计算模型参数量 (Params) 和计算量 (GFLOPS)"""
    model.eval()
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    # GFLOPS 估算：只统计 Conv2d / Linear，避免 thop hook 兼容性问题
    total_ops = 0.0

    def conv_hook(module, inputs, output):
        nonlocal total_ops
        if not isinstance(output, torch.Tensor):
            return
        out_h, out_w = output.shape[-2:]
        kernel_h, kernel_w = module.kernel_size
        in_channels = module.in_channels
        out_channels = module.out_channels
        groups = module.groups
        batch_size = output.shape[0]
        kernel_ops = (kernel_h * kernel_w * in_channels / groups)
        output_elements = batch_size * out_channels * out_h * out_w
        total_ops += float(output_elements * kernel_ops)

    def linear_hook(module, inputs, output):
        nonlocal total_ops
        if not isinstance(output, torch.Tensor):
            return
        batch_size = output.shape[0]
        total_ops += float(module.in_features * module.out_features * batch_size)

    hooks = []
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            hooks.append(m.register_forward_hook(conv_hook))
        elif isinstance(m, nn.Linear):
            hooks.append(m.register_forward_hook(linear_hook))

    dummy = torch.randn(*input_size).to(device)
    try:
        with torch.no_grad():
            _ = model(dummy)
    finally:
        for h in hooks:
            h.remove()

    gflops = total_ops

    return total_params, trainable_params, gflops


@torch.no_grad()
def measure_fps(model, input_size=(3, 384, 384), device="cuda", warmup=20, n_iter=100):
    """测量推理 FPS"""
    model.eval()
    dummy = torch.randn(1, *input_size).to(device)
    is_cuda = str(device).startswith("cuda")

    # warmup
    for _ in range(warmup):
        _ = model(dummy)

    torch.cuda.synchronize() if is_cuda else None
    t0 = time.time()
    for _ in range(n_iter):
        _ = model(dummy)
    torch.cuda.synchronize() if is_cuda else None
    elapsed = time.time() - t0

    fps = n_iter / elapsed
    return fps


# ============================================================
# 4. 训练 & 验证循环
# ============================================================
def train_one_epoch(
    model,
    dataset,
    optimizer,
    criterion,
    device,
    epoch,
    start_batch=0,
):
    model.train()
    total_loss = 0.0
    batch_size = Config.BATCH_SIZE
    order = make_epoch_order(len(dataset), Config.SEED, epoch)
    usable_len = (len(order) // batch_size) * batch_size
    order = order[:usable_len]
    n_batches = len(order) // batch_size
    metrics = StreamingSegMetrics(Config.NUM_CLASSES)

    if start_batch >= n_batches:
        return {
            "loss": 0.0,
            "pix_acc": 0.0,
            "mIoU": 0.0,
            "mDice": 0.0,
            "last_batch_idx": n_batches - 1,
        }

    for batch_idx in range(start_batch, n_batches):
        batch_indices = order[batch_idx * batch_size:(batch_idx + 1) * batch_size]
        batch = [dataset[i] for i in batch_indices]
        images = torch.stack([item[0] for item in batch]).to(device)
        masks = torch.stack([item[1] for item in batch]).to(device)
        paths = [item[2] for item in batch]

        optimizer.zero_grad()
        outputs = model(images)                     # [B, 6, H, W]
        loss    = criterion(outputs, masks)

        loss.backward()
        optimizer.step()

        metrics.update(outputs.detach().cpu().numpy(), masks.detach().cpu().numpy())
        pix_acc, miou, mdice, _, _ = metrics.compute()

        total_loss += loss.item()

        if batch_idx % 20 == 0:
            print(f"  Epoch {epoch} [{batch_idx}/{n_batches}]  "
                  f"Loss={loss.item():.4f}  mIoU={miou:.4f}  mDice={mdice:.4f}")

    pix_acc, mIou, mDice, _, _ = metrics.compute()
    return {
        "loss":    total_loss    / n_batches,
        "pix_acc": pix_acc,
        "mIoU":    mIou,
        "mDice":   mDice,
        "last_batch_idx": n_batches - 1,
    }


@torch.no_grad()
def validate(model, loader, criterion, device):
    model.eval()
    total_loss = 0.0
    n_batches = len(loader)
    metrics = StreamingSegMetrics(Config.NUM_CLASSES)

    for images, masks, paths in loader:
        images = images.to(device)
        masks  = masks.to(device)

        outputs = model(images)
        loss    = criterion(outputs, masks)

        metrics.update(outputs.detach().cpu().numpy(), masks.detach().cpu().numpy())
        pix_acc, miou, mdice, ious, dices = metrics.compute()

        total_loss += loss.item()

    pix_acc, mIou, mDice, mean_ious, mean_dices = metrics.compute()
    mPA, class_accs = class_accuracy_from_confusion_matrix(metrics.cm)
    return {
        "loss":    total_loss    / n_batches,
        "pix_acc": pix_acc,
        "mIoU":    mIou,
        "mDice":   mDice,
        "mPA":     mPA,
        "class_ious": mean_ious,
        "class_dices": mean_dices,
        "class_accs": class_accs,
    }


# ============================================================
# 5. 可视化预测结果
# ============================================================
def colorize_mask(mask, palette):
    """单通道 mask → RGB 彩色图"""
    h, w = mask.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    for cls_id, color in enumerate(palette):
        rgb[mask == cls_id] = color
    return rgb


@torch.no_grad()
def save_predictions(model, loader, save_dir, device, max_save=8):
    model.eval()
    os.makedirs(save_dir, exist_ok=True)
    saved = 0

    for images, masks, paths in loader:
        if saved >= max_save: break
        images = images.to(device)
        outputs = model(images)
        preds = outputs.argmax(dim=1).cpu().numpy()

        for i in range(images.size(0)):
            if saved >= max_save: break

            # 反标准化
            img_np = images[i].cpu().numpy().transpose(1, 2, 0)
            img_np = img_np * np.array([0.229, 0.224, 0.225]) + np.array([0.485, 0.456, 0.406])
            img_np = (np.clip(img_np, 0, 1) * 255).astype(np.uint8)

            pred_rgb = colorize_mask(preds[i], Config.PALETTE)
            true_rgb = colorize_mask(masks[i].numpy(),  Config.PALETTE)

            # 拼接横排
            canvas = np.concatenate([img_np, pred_rgb, true_rgb], axis=1)
            fname = os.path.basename(paths[i])
            Image.fromarray(canvas).save(os.path.join(save_dir, f"pred_{fname}"))
            saved += 1
    print(f"[可视化] 保存 {saved} 张预测结果 → {save_dir}")


def cleanup_old_periodic_checkpoints(save_dir, keep_name=None):
    if not os.path.isdir(save_dir):
        return
    for name in os.listdir(save_dir):
        if not (name.startswith("checkpoint_epoch") and name.endswith(".pth")):
            continue
        if keep_name and name == keep_name:
            continue
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass


def find_latest_checkpoint(save_dir):
    candidates = []
    if not os.path.isdir(save_dir):
        return None
    for name in os.listdir(save_dir):
        if not (name.startswith("checkpoint_epoch") and name.endswith(".pth")):
            continue
        stem = name[len("checkpoint_epoch"):-4]
        epoch_part = stem
        batch_part = 10**9
        if "_batch" in stem:
            epoch_part, batch_part_str = stem.split("_batch", 1)
            if not batch_part_str.isdigit():
                continue
            batch_part = int(batch_part_str)
        if epoch_part.isdigit():
            candidates.append((int(epoch_part), batch_part, os.path.join(save_dir, name)))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[-1][2]
    last_ckpt = os.path.join(save_dir, "last_checkpoint.pth")
    return last_ckpt if os.path.exists(last_ckpt) else None


def make_epoch_order(num_samples, seed, epoch):
    rng = random.Random(seed + epoch * 10007)
    order = list(range(num_samples))
    rng.shuffle(order)
    return order


def save_training_checkpoint(path, model, optimizer, scheduler, epoch, batch_idx,
                             best_epoch, best_val_miou, epoch_complete,
                             include_optimizer=True):
    ckpt = {
        "epoch": epoch,
        "batch_idx": batch_idx,
        "epoch_complete": epoch_complete,
        "model_state": model.state_dict(),
        "scheduler_state": scheduler.state_dict() if scheduler is not None else None,
        "best_epoch": best_epoch,
        "best_val_miou": best_val_miou,
    }
    if include_optimizer and optimizer is not None:
        ckpt["optimizer_state"] = optimizer.state_dict()
    torch.save(ckpt, path)


def load_training_checkpoint(path, model, optimizer, scheduler, device):
    ckpt = torch.load(path, map_location=device)
    if "model_state" in ckpt:
        model.load_state_dict(ckpt["model_state"])
    if optimizer is not None and "optimizer_state" in ckpt:
        optimizer.load_state_dict(ckpt["optimizer_state"])
    if scheduler is not None and "scheduler_state" in ckpt and ckpt["scheduler_state"] is not None:
        scheduler.load_state_dict(ckpt["scheduler_state"])

    epoch_complete = bool(ckpt.get("epoch_complete", True))
    epoch = int(ckpt.get("epoch", 0))
    batch_idx = int(ckpt.get("batch_idx", -1))
    if epoch_complete:
        start_epoch = epoch + 1
        start_batch = 0
    else:
        start_epoch = epoch
        start_batch = batch_idx + 1

    best_val_miou = float(ckpt.get("best_val_miou", ckpt.get("val_miou", 0.0)))
    best_epoch = int(ckpt.get("best_epoch", epoch))
    return start_epoch, start_batch, best_epoch, best_val_miou, ckpt


# ============================================================
# 6. 主训练流程
# ============================================================
def main():
    print("=" * 60, flush=True)
    print("  ResNet50 FCN 渗水/裂缝分割 — 统一训练框架", flush=True)
    print("  num_classes=6 | input=384x384 | epochs=200 | 700/150/150", flush=True)
    print("=" * 60, flush=True)
    print(f"  DEVICE    : {Config.DEVICE}", flush=True)
    print(f"  PRETRAINED: {Config.PRETRAINED}", flush=True)
    print(f"  SAVE DIR  : {Config.SAVE_DIR}", flush=True)
    print(f"  Classes   : {Config.CLASSES}", flush=True)
    print("", flush=True)

    # ---- 数据 ----
    all_pairs = discover_samples(Config.DATA_ROOT, label_mode=Config.LABEL_MODE)
    train_pairs, val_pairs, test_pairs = split_dataset(
        all_pairs,
        seed=Config.SEED,
        train_n=Config.TRAIN_N,
        val_n=Config.VAL_N,
        test_n=Config.TEST_N,
    )

    print(f"\n[TRAIN] {len(train_pairs)} | [VAL] {len(val_pairs)} | [TEST] {len(test_pairs)}")

    # Transforms
    eval_tf = transforms.Compose([
        transforms.Resize(Config.INPUT_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    train_ds = CrackDataset(train_pairs, eval_tf, label_strategy="auto", is_train=True)
    val_ds   = CrackDataset(val_pairs,   eval_tf, label_strategy="auto", is_train=False)
    test_ds  = CrackDataset(test_pairs,  eval_tf, label_strategy="auto", is_train=False)

    train_loader = DataLoader(train_ds, batch_size=Config.BATCH_SIZE, shuffle=True,
                              num_workers=Config.NUM_WORKERS, pin_memory=True, drop_last=True)
    val_loader   = DataLoader(val_ds,   batch_size=Config.BATCH_SIZE, shuffle=False,
                              num_workers=Config.NUM_WORKERS, pin_memory=True)
    test_loader  = DataLoader(test_ds,  batch_size=Config.BATCH_SIZE, shuffle=False,
                              num_workers=Config.NUM_WORKERS, pin_memory=True)

    # ---- 模型 ----
    print("\n[模型] 构建 ResNet50-FCN ...")
    model = ResNet50SegmentationModel(
        num_classes=Config.NUM_CLASSES,
        backbone="fcn",
        pretrained_path=Config.PRETRAINED
    ).to(Config.DEVICE)

    # ---- 参数量（跳过 thop，避免 hook 兼容性错误）----
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"[模型] 总参数量: {total_params/1e6:.2f}M | 可训练: {trainable_params/1e6:.2f}M")
    print(f"[模型] GFLOPS: 跳过 thop（兼容性），将在评估阶段单独计算")

    # ---- 优化器 & 调度器 ----
    optimizer = AdamW(model.parameters(), lr=Config.LR, weight_decay=Config.WEIGHT_DECAY)
    scheduler = CosineAnnealingLR(optimizer, T_max=Config.EPOCHS, eta_min=1e-6)
    criterion  = nn.CrossEntropyLoss()

    # ---- 训练循环 ----
    best_val_miou = 0.0
    best_epoch    = 0
    start_epoch   = 1
    start_batch   = 0
    history = {"train": [], "val": []}

    if Config.AUTO_RESUME:
        resume_path = find_latest_checkpoint(Config.SAVE_DIR)
        if resume_path:
            start_epoch, start_batch, best_epoch, best_val_miou, ckpt = load_training_checkpoint(
                resume_path, model, optimizer, scheduler, Config.DEVICE
            )
            print(
                f"[恢复] 从 {resume_path} 继续训练，start_epoch={start_epoch}, "
                f"start_batch={start_batch}, best_val_miou={best_val_miou:.4f}",
                flush=True,
            )

    print(f"\n{'='*60}")
    print(f" 开始训练 | {Config.EPOCHS} epochs | batch={Config.BATCH_SIZE} | lr={Config.LR}")
    print(f"{'='*60}\n")

    for epoch in range(start_epoch, Config.EPOCHS + 1):
        t_start = time.time()
        try:
            train_metrics = train_one_epoch(
                model,
                train_ds,
                optimizer,
                criterion,
                Config.DEVICE,
                epoch,
                start_batch=start_batch if epoch == start_epoch else 0,
            )
        except KeyboardInterrupt:
            print("\n[中断] 已停止训练。若需要续跑，使用最近保存的 last_checkpoint.pth。", flush=True)
            return

        start_batch = 0
        val_metrics   = validate(model, val_loader, criterion, Config.DEVICE)

        scheduler.step()

        elapsed = time.time() - t_start
        current_lr = optimizer.param_groups[0]["lr"]

        # 日志
        line = (f"Epoch {epoch:03d}/{Config.EPOCHS} ({elapsed:.0f}s) | "
                f"lr={current_lr:.2e} | "
                f"Train[Loss={train_metrics['loss']:.4f}, mIoU={train_metrics['mIoU']:.4f}, "
                f"mDice={train_metrics['mDice']:.4f}] | "
                f"Val[Loss={val_metrics['loss']:.4f}, mIoU={val_metrics['mIoU']:.4f}, "
                f"mDice={val_metrics['mDice']:.4f}, PA={val_metrics['pix_acc']:.4f}]")
        print(line)

        history["train"].append(train_metrics)
        history["val"].append(val_metrics)

        # 保存最佳模型
        if val_metrics["mIoU"] > best_val_miou:
            best_val_miou = val_metrics["mIoU"]
            best_epoch = epoch
            best_path = os.path.join(Config.SAVE_DIR, "best_model.pth")
            torch.save({
                "epoch": epoch,
                "model_state": model.state_dict(),
                "optimizer_state": optimizer.state_dict(),
                "scheduler_state": scheduler.state_dict(),
                "val_miou": best_val_miou,
                "val_mdice": val_metrics["mDice"],
                "best_epoch": best_epoch,
                "best_val_miou": best_val_miou,
                "config": {k: str(v) for k, v in vars(Config).items() if not k.startswith("_")},
            }, best_path)
            print(f"  ★ New Best! mIoU={best_val_miou:.4f} → {best_path}")

        last_ckpt_path = os.path.join(Config.SAVE_DIR, "last_checkpoint.pth")
        save_training_checkpoint(
            last_ckpt_path,
            model,
            optimizer,
            scheduler,
            epoch,
            train_metrics.get("last_batch_idx", -1),
            best_epoch,
            best_val_miou,
            epoch_complete=True,
        )
        if epoch % Config.CHECKPOINT_EVERY_EPOCHS == 0:
            ckpt_path = os.path.join(Config.SAVE_DIR, f"checkpoint_epoch{epoch}.pth")
            save_training_checkpoint(
                ckpt_path,
                model,
                optimizer,
                scheduler,
                epoch,
                train_metrics.get("last_batch_idx", -1),
                best_epoch,
                best_val_miou,
                epoch_complete=True,
            )
            print(f"  [Checkpoint] epoch={epoch}")
            cleanup_old_periodic_checkpoints(Config.SAVE_DIR, keep_name=os.path.basename(ckpt_path))

    # ============================================================
    # 9. 测试集评估 + 完整指标报告
    # ============================================================
    print(f"\n{'='*60}")
    print(" 测试集评估")
    print(f"{'='*60}")

    # 加载最佳模型
    best_path = os.path.join(Config.SAVE_DIR, "best_model.pth")
    if os.path.exists(best_path):
        ckpt = torch.load(best_path, map_location=Config.DEVICE)
        model.load_state_dict(ckpt["model_state"])

    test_metrics = validate(model, test_loader, criterion, Config.DEVICE)

    _, _, gflops = compute_model_stats(
        model,
        input_size=(1, 3, *Config.INPUT_SIZE),
        device=Config.DEVICE
    )

    # FPS
    fps = measure_fps(model, input_size=(3, *Config.INPUT_SIZE),
                      device=Config.DEVICE, warmup=20, n_iter=100)

    # 类别名
    class_names = Config.CLASSES

    print(f"\n{'='*60}")
    print("Unified Baseline Evaluation Report")
    print("=" * 60)
    print(f"mIoU      : {test_metrics['mIoU']:.4f}")
    print(f"mDice     : {test_metrics['mDice']:.4f}")
    print(f"PA        : {test_metrics['pix_acc']:.4f}")
    print(f"mPA       : {test_metrics['mPA']:.4f}")
    print(f"Params(M) : {total_params/1e6:.3f}")
    print(f"GFLOPs    : {gflops/1e9:.3f}")
    print(f"FPS       : {fps:.2f}")
    print("-" * 60)
    print("Per-class IoU / Dice / Accuracy:")

    for i, (cls, iou, dice, acc) in enumerate(zip(
        class_names,
        test_metrics["class_ious"],
        test_metrics["class_dices"],
        test_metrics["class_accs"],
    )):
        print(f"  [{i}] {cls:<12s} IoU={iou:.4f}  Dice={dice:.4f}  Acc={acc:.4f}")

    # 保存可视化
    save_dir = os.path.join(Config.SAVE_DIR, "predictions")
    save_predictions(model, test_loader, save_dir, Config.DEVICE, max_save=16)

    # 保存完整报告
    report_path = os.path.join(Config.SAVE_DIR, "eval_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"实验名称: {Config.EXP_NAME}\n")
        f.write(f"预训练权重: {Config.PRETRAINED}\n")
        f.write(f"类别: {list(Config.CLASSES)}\n")
        f.write(f"输入尺寸: {Config.INPUT_SIZE}\n")
        f.write(f"Epochs: {Config.EPOCHS}\n")
        f.write(f"Batch Size: {Config.BATCH_SIZE}\n")
        f.write(f"Learning Rate: {Config.LR}\n")
        f.write(f"数据集划分: {Config.TRAIN_N}/{Config.VAL_N}/{Config.TEST_N}\n\n")
        f.write("============================================================\n")
        f.write("Unified Baseline Evaluation Report\n")
        f.write("============================================================\n")
        f.write(f"mIoU      : {test_metrics['mIoU']:.4f}\n")
        f.write(f"mDice     : {test_metrics['mDice']:.4f}\n")
        f.write(f"PA        : {test_metrics['pix_acc']:.4f}\n")
        f.write(f"mPA       : {test_metrics['mPA']:.4f}\n")
        f.write(f"Params(M) : {total_params/1e6:.3f}\n")
        f.write(f"GFLOPs    : {gflops/1e9:.3f}\n")
        f.write(f"FPS       : {fps:.2f}\n")
        f.write("------------------------------------------------------------\n")
        f.write("Per-class IoU / Dice / Accuracy:\n")
        for i, (cls, iou, dice, acc) in enumerate(zip(
            class_names,
            test_metrics["class_ious"],
            test_metrics["class_dices"],
            test_metrics["class_accs"],
        )):
            f.write(f"  [{i}] {cls:<12s} IoU={iou:.4f}  Dice={dice:.4f}  Acc={acc:.4f}\n")

    print(f"\n[完成] 评估报告 → {report_path}")
    print(f"[完成] 预测可视化 → {save_dir}")


if __name__ == "__main__":
    main()
