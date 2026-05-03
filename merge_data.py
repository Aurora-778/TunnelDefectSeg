"""
merge_data.py
==================================================
把 Tunnel200 dataset (PNG) 与现有 .npy 合并, 重新划分 train/val/test.

用法:
    python merge_data.py

输入:
    data/npy/data_train.npy, mask_train.npy, ...  (旧数据, 200 张)
    data/Tunnel200 dataset/images_448/*.png        (新数据, 200 张)
    data/Tunnel200 dataset/masks_448/*.png

输出:
    data/npy/data_train.npy  — 覆盖, 合并后的训练集
    data/npy/mask_train.npy
    data/npy/data_val.npy
    data/npy/mask_val.npy
    data/npy/data_test.npy
    data/npy/mask_test.npy

    data/npy/backup/         — 旧 .npy 的备份
"""
import os
import shutil
import numpy as np
from PIL import Image
from pathlib import Path

# ============================================================
# 配置
# ============================================================
NPY_DIR = 'data/npy'
NEW_IMG_DIR = 'data/Tunnel200 dataset/images_448'
NEW_MASK_DIR = 'data/Tunnel200 dataset/masks_448'

TARGET_SIZE = 512       # 与旧数据对齐 (旧数据是 512×512)
MASK_THRESHOLD = 128    # mask 二值化阈值
TRAIN_RATIO = 0.7
VAL_RATIO = 0.15
SEED = 42


def load_old_npy():
    """加载旧的 .npy 数据"""
    splits = {}
    for split in ('train', 'val', 'test'):
        dp = os.path.join(NPY_DIR, f'data_{split}.npy')
        mp = os.path.join(NPY_DIR, f'mask_{split}.npy')
        if os.path.exists(dp) and os.path.exists(mp):
            splits[split] = {
                'images': np.load(dp),
                'masks': np.load(mp),
            }
            print(f'  旧 {split}: {splits[split]["images"].shape[0]} 张')
    return splits


def load_new_pngs():
    """加载新的 PNG 图片, resize 到 TARGET_SIZE, mask 二值化"""
    img_files = sorted(Path(NEW_IMG_DIR).glob('*.png'))
    mask_files = sorted(Path(NEW_MASK_DIR).glob('*.png'))

    # 按文件名匹配
    img_dict = {f.stem: f for f in img_files}
    mask_dict = {f.stem: f for f in mask_files}
    common = sorted(set(img_dict.keys()) & set(mask_dict.keys()))

    print(f'  新数据: images={len(img_files)}, masks={len(mask_files)}, 匹配={len(common)}')

    images = []
    masks = []
    for name in common:
        # 图像
        img = Image.open(img_dict[name]).convert('RGB')
        img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.BILINEAR)
        images.append(np.array(img, dtype=np.float32))

        # mask: 取第一通道, 阈值二值化
        mask = Image.open(mask_dict[name]).convert('L')
        mask = mask.resize((TARGET_SIZE, TARGET_SIZE), Image.NEAREST)
        mask_arr = (np.array(mask) >= MASK_THRESHOLD).astype(np.int64)
        masks.append(mask_arr)

    images = np.stack(images)   # (N, 512, 512, 3) float32
    masks = np.stack(masks)     # (N, 512, 512) int64
    print(f'  新数据加载完: images {images.shape}, masks {masks.shape}')
    print(f'  mask 裂缝像素比例: {masks.mean():.4f}')
    return images, masks


def merge_and_split(old_splits, new_images, new_masks):
    """合并旧数据 + 新数据, 重新随机划分"""
    # 把所有旧数据 concat
    all_images = []
    all_masks = []
    for split in ('train', 'val', 'test'):
        if split in old_splits:
            all_images.append(old_splits[split]['images'])
            all_masks.append(old_splits[split]['masks'])

    all_images.append(new_images)
    all_masks.append(new_masks)

    all_images = np.concatenate(all_images, axis=0)
    all_masks = np.concatenate(all_masks, axis=0)
    print(f'\n合并后总计: {all_images.shape[0]} 张')

    # 随机打乱
    rng = np.random.RandomState(SEED)
    indices = rng.permutation(len(all_images))
    all_images = all_images[indices]
    all_masks = all_masks[indices]

    # 划分
    n = len(all_images)
    n_train = int(n * TRAIN_RATIO)
    n_val = int(n * VAL_RATIO)

    splits = {
        'train': (all_images[:n_train], all_masks[:n_train]),
        'val':   (all_images[n_train:n_train+n_val], all_masks[n_train:n_train+n_val]),
        'test':  (all_images[n_train+n_val:], all_masks[n_train+n_val:]),
    }

    for split, (imgs, msks) in splits.items():
        print(f'  {split}: {len(imgs)} 张')

    return splits


def main():
    print('=== 加载旧数据 ===')
    old = load_old_npy()

    print('\n=== 加载新数据 ===')
    new_images, new_masks = load_new_pngs()

    print('\n=== 合并 + 重新划分 ===')
    splits = merge_and_split(old, new_images, new_masks)

    # 备份旧 npy
    backup_dir = os.path.join(NPY_DIR, 'backup')
    os.makedirs(backup_dir, exist_ok=True)
    for f in Path(NPY_DIR).glob('*.npy'):
        shutil.copy2(f, os.path.join(backup_dir, f.name))
    print(f'\n旧 .npy 已备份到 {backup_dir}')

    # 保存新 npy
    for split, (imgs, msks) in splits.items():
        np.save(os.path.join(NPY_DIR, f'data_{split}.npy'), imgs)
        np.save(os.path.join(NPY_DIR, f'mask_{split}.npy'), msks)
        print(f'  已保存 data_{split}.npy ({imgs.shape}), mask_{split}.npy ({msks.shape})')

    print('\n=== 完成 ===')
    total = sum(len(s[0]) for s in splits.values())
    print(f'总计 {total} 张, 可以开始训练:')
    print('  python train.py --data_path .\\data')


if __name__ == '__main__':
    main()
