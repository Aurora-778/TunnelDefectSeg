from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torchvision import transforms

from train_resnet50 import Config, ResNet50SegmentationModel, colorize_mask


CUSTOM_PALETTE = [
    [0, 0, 0],
    [0, 200, 255],
    [255, 80, 80],
    [140, 90, 255],
    [255, 200, 0],
    [60, 220, 90],
]


def load_model():
    model = ResNet50SegmentationModel(
        num_classes=Config.NUM_CLASSES,
        backbone="fcn",
        pretrained_path=Config.PRETRAINED,
    ).to(Config.DEVICE)

    ckpt_path = Path(Config.SAVE_DIR) / "best_model.pth"
    if not ckpt_path.exists():
        raise FileNotFoundError(f"Missing checkpoint: {ckpt_path}")
    ckpt = torch.load(ckpt_path, map_location=Config.DEVICE)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model


def preprocess_image(img_path: Path):
    tf = transforms.Compose([
        transforms.Resize(Config.INPUT_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    img = Image.open(img_path).convert("RGB")
    return tf(img), np.array(img.resize(Config.INPUT_SIZE, Image.BILINEAR))


@torch.no_grad()
def infer_one(model, img_path: Path, out_dir: Path):
    tensor, raw_resized = preprocess_image(img_path)
    x = tensor.unsqueeze(0).to(Config.DEVICE)
    logits = model(x)
    pred = logits.argmax(dim=1)[0].cpu().numpy().astype(np.uint8)

    pred_rgb = colorize_mask(pred, CUSTOM_PALETTE)
    overlay = (0.55 * raw_resized + 0.45 * pred_rgb).astype(np.uint8)

    stem = img_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pred_rgb).save(out_dir / f"{stem}_pred.png")
    Image.fromarray(overlay).save(out_dir / f"{stem}_overlay.png")

    uniq, counts = np.unique(pred, return_counts=True)
    stats = {int(u): int(c) for u, c in zip(uniq.tolist(), counts.tolist())}
    return pred, stats


def main():
    img_paths = [
        Path(r"C:\Users\26822\Documents\Tencent Files\2682215046\nt_qq\nt_data\Pic\2026-05\Ori\db4cc61217443be8d25ff9cf847db177.jpg"),
        Path(r"C:\Users\26822\Documents\Tencent Files\2682215046\nt_qq\nt_data\Pic\2026-05\Ori\63144d11368eb802d7d8ab0191ec1387.jpg"),
        Path(r"C:\Users\26822\Documents\Tencent Files\2682215046\nt_qq\nt_data\Pic\2026-05\Ori\52f7e479a41fd29fc93c331dbe9a06f3.jpg"),
        Path(r"C:\Users\26822\Documents\Tencent Files\2682215046\nt_qq\nt_data\Pic\2026-05\Ori\352e095ed37ec9cfa13db8a4ecc29d94.jpg"),
        Path(r"C:\Users\26822\Documents\Tencent Files\2682215046\nt_qq\nt_data\Pic\2026-05\Ori\8c143ab7bf39cfda8c3380b529791982.jpg"),
    ]

    out_dir = Path(Config.SAVE_DIR) / "inference_5"
    model = load_model()

    print("=" * 60)
    print("Five-image inference")
    print(f"Checkpoint: {Path(Config.SAVE_DIR) / 'best_model.pth'}")
    print(f"Output dir: {out_dir}")
    print("=" * 60)

    for idx, img_path in enumerate(img_paths, 1):
        if not img_path.exists():
            print(f"[{idx}] missing: {img_path}")
            continue
        pred, stats = infer_one(model, img_path, out_dir)
        print(f"[{idx}] {img_path.name}")
        print(f"    predicted classes pixels: {stats}")


if __name__ == "__main__":
    main()
