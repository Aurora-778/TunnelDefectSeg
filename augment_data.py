"""
隧道病害数据增强脚本
====================
扫描每个病害文件夹, 不足250张的自动增强到300张。
图片和mask同步增强, 保证一一对应。

用法:
    python augment_data.py --data_root ./data --target 300 --threshold 250

增强方式:
    1. 随机水平翻转
    2. 随机垂直翻转
    3. 随机旋转 (90/180/270度)
    4. 随机旋转 (任意角度 -30~+30度)
    5. 随机亮度/对比度调整 (仅图片)
    6. 随机裁剪+缩放
    7. 随机高斯噪声 (仅图片)
    8. 组合增强 (多种方式叠加)
"""

import os
import sys
import glob
import random
import argparse
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from scipy import ndimage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))
from configs.class_config import FOLDER_TO_CLASS, CLASS_NAMES


# ============================================================
# 增强函数: 同时处理图片和mask
# ============================================================

def augment_hflip(img, mask):
    """水平翻转"""
    return img.transpose(Image.FLIP_LEFT_RIGHT), mask.transpose(Image.FLIP_LEFT_RIGHT)


def augment_vflip(img, mask):
    """垂直翻转"""
    return img.transpose(Image.FLIP_TOP_BOTTOM), mask.transpose(Image.FLIP_TOP_BOTTOM)


def augment_rot90(img, mask):
    """旋转90度"""
    return img.transpose(Image.ROTATE_90), mask.transpose(Image.ROTATE_90)


def augment_rot180(img, mask):
    """旋转180度"""
    return img.transpose(Image.ROTATE_180), mask.transpose(Image.ROTATE_180)


def augment_rot270(img, mask):
    """旋转270度"""
    return img.transpose(Image.ROTATE_270), mask.transpose(Image.ROTATE_270)


def augment_random_rotate(img, mask):
    """随机小角度旋转 (-30 ~ +30度)"""
    angle = random.uniform(-30, 30)
    img_rot = img.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=(0, 0, 0))
    mask_rot = mask.rotate(angle, resample=Image.NEAREST, expand=False, fillcolor=0)
    return img_rot, mask_rot


def augment_brightness(img, mask):
    """随机亮度 (仅图片)"""
    factor = random.uniform(0.7, 1.3)
    enhancer = ImageEnhance.Brightness(img)
    return enhancer.enhance(factor), mask


def augment_contrast(img, mask):
    """随机对比度 (仅图片)"""
    factor = random.uniform(0.7, 1.3)
    enhancer = ImageEnhance.Contrast(img)
    return enhancer.enhance(factor), mask


def augment_color(img, mask):
    """随机色彩饱和度 (仅图片)"""
    factor = random.uniform(0.7, 1.3)
    enhancer = ImageEnhance.Color(img)
    return enhancer.enhance(factor), mask


def augment_gaussian_noise(img, mask):
    """添加高斯噪声 (仅图片)"""
    img_np = np.array(img, dtype=np.float32)
    noise = np.random.normal(0, random.uniform(5, 15), img_np.shape)
    img_np = np.clip(img_np + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(img_np), mask


def augment_blur(img, mask):
    """轻微模糊 (仅图片)"""
    radius = random.uniform(0.5, 1.5)
    return img.filter(ImageFilter.GaussianBlur(radius=radius)), mask


def augment_random_crop(img, mask):
    """随机裁剪后缩放回原尺寸"""
    w, h = img.size
    # 裁剪70%~90%的区域
    crop_ratio = random.uniform(0.70, 0.90)
    cw, ch = int(w * crop_ratio), int(h * crop_ratio)
    x = random.randint(0, w - cw)
    y = random.randint(0, h - ch)
    
    img_crop = img.crop((x, y, x + cw, y + ch)).resize((w, h), Image.BILINEAR)
    mask_crop = mask.crop((x, y, x + cw, y + ch)).resize((w, h), Image.NEAREST)
    return img_crop, mask_crop


def augment_combined(img, mask):
    """组合增强: 随机选2~3种方式叠加"""
    spatial_augs = [augment_hflip, augment_vflip, augment_rot90, augment_rot180,
                    augment_rot270, augment_random_rotate, augment_random_crop]
    color_augs = [augment_brightness, augment_contrast, augment_color,
                  augment_gaussian_noise, augment_blur]
    
    # 随机选1个空间增强
    spatial_fn = random.choice(spatial_augs)
    img, mask = spatial_fn(img, mask)
    
    # 随机选1~2个颜色增强
    num_color = random.randint(1, 2)
    color_fns = random.sample(color_augs, num_color)
    for fn in color_fns:
        img, mask = fn(img, mask)
    
    return img, mask


# 所有增强方式列表
ALL_AUGMENTATIONS = [
    ('hflip',        augment_hflip),
    ('vflip',        augment_vflip),
    ('rot90',        augment_rot90),
    ('rot180',       augment_rot180),
    ('rot270',       augment_rot270),
    ('randrot',      augment_random_rotate),
    ('brightness',   augment_brightness),
    ('contrast',     augment_contrast),
    ('color',        augment_color),
    ('noise',        augment_gaussian_noise),
    ('blur',         augment_blur),
    ('crop',         augment_random_crop),
    ('combined_1',   augment_combined),
    ('combined_2',   augment_combined),
    ('combined_3',   augment_combined),
]


def count_images(img_dir):
    """统计图片数量"""
    count = 0
    for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']:
        count += len(glob.glob(os.path.join(img_dir, ext)))
    return count


def get_image_mask_pairs(img_dir, mask_dir):
    """获取配对的图片和mask路径"""
    pairs = []
    img_files = []
    for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']:
        img_files.extend(glob.glob(os.path.join(img_dir, ext)))
    img_files = sorted(list(set(img_files)))
    
    for img_path in img_files:
        basename = os.path.splitext(os.path.basename(img_path))[0]
        # 查找对应mask
        for msuffix in ['.png', '.jpg', '.bmp', '.tif']:
            candidate = os.path.join(mask_dir, basename + msuffix)
            if os.path.exists(candidate):
                pairs.append((img_path, candidate))
                break
    
    return pairs


def augment_folder(folder_name, data_root, target_count=300, threshold=250):
    """
    对单个文件夹进行数据增强
    
    Args:
        folder_name: 文件夹名 (如 'crack')
        data_root: 数据根目录
        target_count: 增强后的目标数量
        threshold: 少于此数量才增强
    
    Returns:
        生成的增强样本数
    """
    img_dir = os.path.join(data_root, folder_name, 'images')
    mask_dir = os.path.join(data_root, folder_name, 'labels')
    
    if not os.path.exists(img_dir) or not os.path.exists(mask_dir):
        print(f'  [SKIP] {folder_name}: 目录不存在')
        return 0
    
    pairs = get_image_mask_pairs(img_dir, mask_dir)
    current_count = len(pairs)
    
    if current_count == 0:
        print(f'  [SKIP] {folder_name}: 没有找到配对的图片和mask')
        return 0
    
    if current_count >= threshold:
        print(f'  [SKIP] {folder_name}: 已有 {current_count} 张, >= {threshold}, 无需增强')
        return 0
    
    need_count = target_count - current_count
    print(f'  [AUG]  {folder_name}: 当前 {current_count} 张, 需增强 {need_count} 张到 {target_count}')
    
    generated = 0
    aug_idx = 0
    
    while generated < need_count:
        # 随机选一张原图
        src_img_path, src_mask_path = random.choice(pairs)
        basename = os.path.splitext(os.path.basename(src_img_path))[0]
        img_ext = os.path.splitext(src_img_path)[1]
        mask_ext = os.path.splitext(src_mask_path)[1]
        
        # 随机选一种增强方式
        aug_name, aug_fn = random.choice(ALL_AUGMENTATIONS)
        
        # 读取图片和mask
        try:
            img = Image.open(src_img_path).convert('RGB')
            mask = Image.open(src_mask_path).convert('L')
        except Exception as e:
            print(f'    读取失败: {src_img_path}, {e}')
            continue
        
        # 执行增强
        try:
            img_aug, mask_aug = aug_fn(img, mask)
        except Exception as e:
            print(f'    增强失败: {aug_name}, {e}')
            continue
        
        # 保存增强后的图片
        aug_suffix = f'_aug{aug_idx:04d}_{aug_name}'
        new_img_name = f'{basename}{aug_suffix}{img_ext}'
        new_mask_name = f'{basename}{aug_suffix}{mask_ext}'
        
        new_img_path = os.path.join(img_dir, new_img_name)
        new_mask_path = os.path.join(mask_dir, new_mask_name)
        
        # 避免覆盖已有文件
        if os.path.exists(new_img_path):
            aug_idx += 1
            continue
        
        img_aug.save(new_img_path)
        mask_aug.save(new_mask_path)
        
        generated += 1
        aug_idx += 1
        
        if generated % 50 == 0:
            print(f'    已生成 {generated}/{need_count} ...')
    
    print(f'  [DONE] {folder_name}: 增强完成, 共生成 {generated} 张, 总计 {current_count + generated} 张')
    return generated


def main():
    parser = argparse.ArgumentParser(description='隧道病害数据增强')
    parser.add_argument('--data_root', type=str, default='./data',
                        help='数据根目录 (包含 crack/, leakageB/ 等)')
    parser.add_argument('--target', type=int, default=300,
                        help='增强后的目标数量 (默认300)')
    parser.add_argument('--threshold', type=int, default=250,
                        help='少于此数量才增强 (默认250)')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    
    random.seed(args.seed)
    np.random.seed(args.seed)
    
    print('=' * 60)
    print('隧道病害数据增强工具')
    print(f'数据目录: {args.data_root}')
    print(f'增强阈值: 少于 {args.threshold} 张时增强')
    print(f'目标数量: {args.target} 张')
    print('=' * 60)
    
    # 先统计各文件夹数量
    print('\n--- 增强前统计 ---')
    folder_counts = {}
    for folder_name in FOLDER_TO_CLASS.keys():
        img_dir = os.path.join(args.data_root, folder_name, 'images')
        if os.path.exists(img_dir):
            cnt = count_images(img_dir)
            folder_counts[folder_name] = cnt
            status = '✓ 充足' if cnt >= args.threshold else f'✗ 不足 (需增强 {args.target - cnt} 张)'
            print(f'  {folder_name:25s}: {cnt:4d} 张  {status}')
        else:
            folder_counts[folder_name] = 0
            print(f'  {folder_name:25s}: 目录不存在')
    
    # 执行增强
    print('\n--- 开始增强 ---')
    total_generated = 0
    for folder_name in FOLDER_TO_CLASS.keys():
        generated = augment_folder(
            folder_name, args.data_root,
            target_count=args.target,
            threshold=args.threshold
        )
        total_generated += generated
    
    # 增强后统计
    print('\n--- 增强后统计 ---')
    for folder_name in FOLDER_TO_CLASS.keys():
        img_dir = os.path.join(args.data_root, folder_name, 'images')
        if os.path.exists(img_dir):
            cnt = count_images(img_dir)
            print(f'  {folder_name:25s}: {cnt:4d} 张')
    
    print(f'\n总计新增: {total_generated} 张增强图片')
    print('增强完成!')


if __name__ == '__main__':
    main()
