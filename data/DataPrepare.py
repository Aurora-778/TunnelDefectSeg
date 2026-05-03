"""
隧道病害多类语义分割 - 数据预处理脚本 (更新版)
=================================================
适配新目录结构:
  data/
    crack/           images/ labels/
    leakageB/        images/ labels/
    leakageG/        images/ labels/
    leakageW/        images/ labels/
    lining falling off/  images/ labels/
    segment damage/  images/ labels/

输出 npy 文件用于快速训练加载。

用法:
    python data/DataPrepare.py --data_root ./data
    python data/DataPrepare.py --data_root ./data1
"""

import os
import sys
import glob
import random
import argparse
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from configs.class_config import FOLDER_TO_CLASS, NUM_CLASSES, CLASS_NAMES

# 图片尺寸
HEIGHT = 256
WIDTH = 256


def get_image_mask_pairs(folder_path):
    """
    从 folder_path/images/ 和 folder_path/labels/ 中获取配对
    """
    img_dir = os.path.join(folder_path, 'images')
    mask_dir = os.path.join(folder_path, 'labels')

    if not os.path.exists(img_dir) or not os.path.exists(mask_dir):
        return []

    img_files = []
    for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']:
        img_files.extend(glob.glob(os.path.join(img_dir, ext)))
    img_files = sorted(list(set(img_files)))

    pairs = []
    for img_path in img_files:
        basename = os.path.splitext(os.path.basename(img_path))[0]
        # 查找对应mask
        for msuffix in ['.png', '.jpg', '.bmp', '.tif', '.tiff']:
            candidate = os.path.join(mask_dir, basename + msuffix)
            if os.path.exists(candidate):
                pairs.append((img_path, candidate))
                break

    return pairs


def collect_and_process(data_root, train_ratio=0.7, val_ratio=0.15, seed=42):
    """收集所有样本并划分"""
    all_samples = []

    for folder_name, class_id in FOLDER_TO_CLASS.items():
        folder_path = os.path.join(data_root, folder_name)

        if not os.path.exists(folder_path):
            print(f'  [SKIP] {folder_name}: 目录不存在 ({folder_path})')
            continue

        pairs = get_image_mask_pairs(folder_path)
        for img_path, mask_path in pairs:
            all_samples.append({
                'img_path': img_path,
                'mask_path': mask_path,
                'class_id': class_id,
                'folder': folder_name,
            })

        print(f'  [{folder_name}] class={class_id} ({CLASS_NAMES[class_id]}): {len(pairs)} 样本')

    print(f'\n总样本数: {len(all_samples)}')

    # 按文件夹内部划分
    rng = random.Random(seed)
    folder_groups = {}
    for s in all_samples:
        folder_groups.setdefault(s['folder'], []).append(s)

    train_set, val_set, test_set = [], [], []
    for folder, samples in folder_groups.items():
        rng.shuffle(samples)
        n = len(samples)
        n_train = max(1, int(n * train_ratio))
        n_val = max(1, int(n * val_ratio))
        train_set.extend(samples[:n_train])
        val_set.extend(samples[n_train:n_train + n_val])
        test_set.extend(samples[n_train + n_val:])

    print(f'训练集: {len(train_set)}, 验证集: {len(val_set)}, 测试集: {len(test_set)}')

    return train_set, val_set, test_set


def samples_to_npy(samples, desc=''):
    """将样本列表转换为numpy数组"""
    n = len(samples)
    if n == 0:
        return np.zeros((0, HEIGHT, WIDTH, 3), dtype=np.float32), \
               np.zeros((0, HEIGHT, WIDTH), dtype=np.int64)

    data = np.zeros((n, HEIGHT, WIDTH, 3), dtype=np.float32)
    masks = np.zeros((n, HEIGHT, WIDTH), dtype=np.int64)

    for idx, s in enumerate(samples):
        if idx % 50 == 0:
            print(f'  [{desc}] {idx}/{n}')

        # 读取图片
        img = Image.open(s['img_path']).convert('RGB')
        img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)
        data[idx] = np.array(img, dtype=np.float32)

        # 读取mask并转为多类
        mask = Image.open(s['mask_path']).convert('L')
        mask = mask.resize((WIDTH, HEIGHT), Image.NEAREST)
        mask_np = np.array(mask, dtype=np.float32)

        seg_mask = np.zeros((HEIGHT, WIDTH), dtype=np.int64)
        seg_mask[mask_np > 127] = s['class_id']
        masks[idx] = seg_mask

    return data, masks


def main():
    parser = argparse.ArgumentParser(description='隧道病害数据预处理')
    parser.add_argument('--data_root', type=str, default='./data',
                        help='数据根目录 (包含 crack/, leakageB/ 等)')
    parser.add_argument('--output_dir', type=str, default=None,
                        help='npy输出目录, 默认 data_root/npy/')
    parser.add_argument('--train_ratio', type=float, default=0.7)
    parser.add_argument('--val_ratio', type=float, default=0.15)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    if args.output_dir is None:
        args.output_dir = os.path.join(args.data_root, 'npy')
    os.makedirs(args.output_dir, exist_ok=True)

    print('=' * 50)
    print('隧道病害数据预处理')
    print(f'数据根目录: {os.path.abspath(args.data_root)}')
    print(f'输出目录: {os.path.abspath(args.output_dir)}')
    print(f'目录结构: data_root/类别名/images/ + labels/')
    print('=' * 50)

    # 检查目录是否存在
    if not os.path.exists(args.data_root):
        print(f'\n[ERROR] 数据根目录不存在: {os.path.abspath(args.data_root)}')
        print(f'请检查路径, 或使用 --data_root 指定正确路径')
        return

    # 列出data_root下的子文件夹
    print(f'\n检测到的子文件夹:')
    for item in sorted(os.listdir(args.data_root)):
        full_path = os.path.join(args.data_root, item)
        if os.path.isdir(full_path):
            has_images = os.path.exists(os.path.join(full_path, 'images'))
            has_labels = os.path.exists(os.path.join(full_path, 'labels'))
            status = '✓' if (has_images and has_labels) else '✗ (缺少images/或labels/)'
            print(f'  {item:25s} {status}')

    # 收集样本
    print()
    train_set, val_set, test_set = collect_and_process(
        args.data_root, args.train_ratio, args.val_ratio, args.seed)

    if len(train_set) == 0:
        print('\n[ERROR] 没有找到任何样本! 请检查:')
        print(f'  1. 数据目录结构是否为: {args.data_root}/crack/images/*.jpg + labels/*.png')
        print(f'  2. 文件夹名是否匹配: {list(FOLDER_TO_CLASS.keys())}')
        return

    # 转为npy
    print('\n处理训练集...')
    train_data, train_mask = samples_to_npy(train_set, 'train')

    print('\n处理验证集...')
    val_data, val_mask = samples_to_npy(val_set, 'val')

    print('\n处理测试集...')
    test_data, test_mask = samples_to_npy(test_set, 'test')

    # 保存
    np.save(os.path.join(args.output_dir, 'data_train.npy'), train_data)
    np.save(os.path.join(args.output_dir, 'mask_train.npy'), train_mask)
    np.save(os.path.join(args.output_dir, 'data_val.npy'), val_data)
    np.save(os.path.join(args.output_dir, 'mask_val.npy'), val_mask)
    np.save(os.path.join(args.output_dir, 'data_test.npy'), test_data)
    np.save(os.path.join(args.output_dir, 'mask_test.npy'), test_mask)

    print('\n' + '=' * 50)
    print('数据预处理完成!')
    print(f'训练集: {train_data.shape}')
    print(f'验证集: {val_data.shape}')
    print(f'测试集: {test_data.shape}')

    # 打印mask类别分布
    unique, counts = np.unique(train_mask, return_counts=True)
    print(f'\n训练集类别分布:')
    for u, c in zip(unique, counts):
        name = CLASS_NAMES.get(int(u), f'class{u}')
        pct = c / train_mask.size * 100
        print(f'  {name}: {c} 像素 ({pct:.1f}%)')

    print(f'\n保存位置: {os.path.abspath(args.output_dir)}')


if __name__ == '__main__':
    main()
