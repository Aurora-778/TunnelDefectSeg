"""
隧道病害多类语义分割 - 数据加载器 v3
=======================================
v3 新增增强方法:
  1. 弹性变形 (Elastic Transform) - 针对裂缝
       模拟裂缝的弯曲形变, 增加几何多样性
       参考: Simard et al., "Best Practices for CNNs", ICDAR 2003
  2. Mosaic 增强 (4 图拼接)
       将 4 张不同病害图拼为一张 2x2 图 (每块 H/2 x W/2)
       显著增加多病害共现场景, 参考 YOLOv4
  3. 改进 Copy-Paste: 增加随机平移偏移
       之前贴在原位, v3 随机平移 ±H/4 使裂缝分布更多样
  4. 色彩抖动增强 (亮度/对比度/饱和度)
       更接近真实隧道照明变化

原有增强全部保留:
  - 翻转 / 旋转 / 缩放裁剪 / 亮度对比度 / 高斯噪声
  - 裂缝 Copy-Paste (已升级)
  - 裂缝 mask 膨胀
"""

import os
import glob
import random
import numpy as np
from PIL import Image
from scipy import ndimage
import torch
from torch.utils.data import Dataset

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from configs.class_config import FOLDER_TO_CLASS, NUM_CLASSES


IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMG_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class TunnelDefectDataset(Dataset):
    """
    Args:
        data_root:              数据根目录
        split:                  'train' / 'val' / 'test'
        img_size:               图像尺寸 (H, W)
        augment:                是否数据增强 (仅 train 生效)
        train_ratio, val_ratio: 数据集划分比例
        seed:                   随机种子
        crack_oversample:       裂缝类训练集过采样倍数
        crack_dilate:           裂缝 mask 膨胀像素数
        crack_copy_paste_prob:  裂缝 Copy-Paste 概率
        crack_elastic_prob:     裂缝弹性变形概率 (v3 新增)
        mosaic_prob:            Mosaic 增强概率 (v3 新增)
    """
    def __init__(self, data_root, split='train', img_size=(384, 384),
                 augment=True, train_ratio=0.7, val_ratio=0.15, seed=42,
                 crack_oversample=3, crack_dilate=1,
                 crack_copy_paste_prob=0.5,
                 crack_elastic_prob=0.3,
                 mosaic_prob=0.2):
        super().__init__()
        self.data_root = data_root
        self.split = split
        self.img_size = img_size
        self.augment = augment and (split == 'train')
        self.crack_dilate = crack_dilate if split == 'train' else 0
        self.crack_copy_paste_prob = crack_copy_paste_prob if split == 'train' else 0.0
        self.crack_elastic_prob = crack_elastic_prob if split == 'train' else 0.0
        self.mosaic_prob = mosaic_prob if split == 'train' else 0.0

        all_samples = self._collect_samples()
        self.samples = self._split_data(all_samples, train_ratio, val_ratio, seed)

        if split == 'train' and crack_oversample > 1:
            self._oversample_class('crack', crack_oversample)

        self.crack_pool = [s for s in self.samples if s['folder'] == 'crack'] \
            if split == 'train' else []

        print(f'[{split.upper()}] 总样本: {len(self.samples)}, '
              f'裂缝捐赠池: {len(self.crack_pool)}')

    # [S5 修复] 新增: Progressive Resizing 真正能切分辨率
    def update_input_size(self, h, w):
        """运行时切换输入分辨率 (供 ProgressiveResizing 调度器调用)"""
        self.img_size = (int(h), int(w))

    def _collect_samples(self):
        all_samples = []
        for folder_name, class_id in FOLDER_TO_CLASS.items():
            img_dir = os.path.join(self.data_root, folder_name, 'images')
            mask_dir = os.path.join(self.data_root, folder_name, 'labels')
            if not os.path.exists(img_dir) or not os.path.exists(mask_dir):
                print(f'  [WARNING] 目录不存在: {img_dir}')
                continue

            img_files = []
            for suffix in ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tif', '*.tiff']:
                img_files.extend(glob.glob(os.path.join(img_dir, suffix)))
            img_files = sorted(set(img_files))

            count = 0
            for img_path in img_files:
                basename = os.path.splitext(os.path.basename(img_path))[0]
                mask_path = None
                for msuffix in ['.png', '.jpg', '.bmp', '.tif', '.tiff']:
                    candidate = os.path.join(mask_dir, basename + msuffix)
                    if os.path.exists(candidate):
                        mask_path = candidate
                        break
                if mask_path is not None:
                    all_samples.append({
                        'img_path': img_path,
                        'mask_path': mask_path,
                        'class_id': class_id,
                        'folder': folder_name
                    })
                    count += 1
            print(f'  [{folder_name}] (class {class_id}): {count} 样本')
        return all_samples

    def _split_data(self, all_samples, train_ratio, val_ratio, seed):
        rng = random.Random(seed)
        folder_samples = {}
        for s in all_samples:
            folder_samples.setdefault(s['folder'], []).append(s)

        train_set, val_set, test_set = [], [], []
        for folder, samples in folder_samples.items():
            rng.shuffle(samples)
            n = len(samples)
            n_train = max(1, int(n * train_ratio))
            n_val = max(1, int(n * val_ratio))
            train_set.extend(samples[:n_train])
            val_set.extend(samples[n_train:n_train + n_val])
            test_set.extend(samples[n_train + n_val:])
        return {'train': train_set, 'val': val_set, 'test': test_set}[self.split]

    def _oversample_class(self, folder_name, factor):
        targets = [s for s in self.samples if s['folder'] == folder_name]
        if not targets:
            return
        original = len(targets)
        self.samples.extend(targets * (factor - 1))
        print(f'  [OVERSAMPLE] {folder_name}: {original} -> {original * factor}')

    def __len__(self):
        return len(self.samples)

    def _load_raw(self, sample):
        img = Image.open(sample['img_path']).convert('RGB')
        img = img.resize((self.img_size[1], self.img_size[0]), Image.BILINEAR)
        img = np.array(img, dtype=np.float32)

        mask = Image.open(sample['mask_path']).convert('L')
        mask = mask.resize((self.img_size[1], self.img_size[0]), Image.NEAREST)
        mask_bin = np.array(mask, dtype=np.uint8) > 127
        return img, mask_bin

    # ------------------------------------------------------------------
    # [v3] 弹性变形 - 针对裂缝的弯曲形变
    # 参考: Simard et al., ICDAR 2003
    # ------------------------------------------------------------------
    @staticmethod
    def _elastic_transform(img, seg, alpha=300, sigma=12, seed=None):
        """
        对图像和分割掩膜施加随机弹性变形.
        alpha: 变形幅度 (越大变形越剧烈)
        sigma: 高斯平滑半径 (越大变形越平滑)
        裂缝场景: alpha=200-400, sigma=10-15
        """
        rng = np.random.RandomState(seed)
        h, w = img.shape[:2]

        dx = rng.rand(h, w).astype(np.float32) * 2 - 1
        dy = rng.rand(h, w).astype(np.float32) * 2 - 1
        dx = ndimage.gaussian_filter(dx, sigma) * alpha
        dy = ndimage.gaussian_filter(dy, sigma) * alpha

        x, y = np.meshgrid(np.arange(w), np.arange(h))
        map_x = np.clip(x + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(y + dy, 0, h - 1).astype(np.float32)

        # 双线性插值图像
        from scipy.ndimage import map_coordinates
        img_deformed = np.stack([
            map_coordinates(img[:, :, c], [map_y.ravel(), map_x.ravel()],
                            order=1, mode='reflect').reshape(h, w)
            for c in range(img.shape[2])
        ], axis=2)

        # 最近邻插值掩膜
        seg_deformed = map_coordinates(
            seg.astype(np.float32), [map_y.ravel(), map_x.ravel()],
            order=0, mode='constant', cval=0).reshape(h, w).astype(np.int64)

        return img_deformed.astype(np.float32), seg_deformed

    # ------------------------------------------------------------------
    # [v3 改进] 裂缝 Copy-Paste - 增加随机平移
    # ------------------------------------------------------------------
    def _crack_copy_paste(self, img, seg):
        if not self.crack_pool:
            return img, seg

        donor = random.choice(self.crack_pool)
        try:
            d_img, d_mask = self._load_raw(donor)
        except Exception:
            return img, seg

        # 随机几何扰动
        if random.random() < 0.5:
            d_img = np.flip(d_img, axis=1); d_mask = np.flip(d_mask, axis=1)
        if random.random() < 0.5:
            d_img = np.flip(d_img, axis=0); d_mask = np.flip(d_mask, axis=0)
        if random.random() < 0.5:
            k = random.randint(1, 3)
            d_img = np.rot90(d_img, k); d_mask = np.rot90(d_mask, k)

        # [v3 新增] 随机平移 ±H/4
        h, w = img.shape[:2]
        shift_y = random.randint(-h // 4, h // 4)
        shift_x = random.randint(-w // 4, w // 4)
        d_img = np.roll(np.roll(d_img, shift_y, axis=0), shift_x, axis=1)
        d_mask = np.roll(np.roll(d_mask, shift_y, axis=0), shift_x, axis=1)

        d_img = np.ascontiguousarray(d_img)
        d_mask = np.ascontiguousarray(d_mask)

        paste_mask = d_mask & (seg == 0)
        if not paste_mask.any():
            return img, seg

        img[paste_mask] = d_img[paste_mask]
        seg[paste_mask] = 1
        return img, seg

    # ------------------------------------------------------------------
    # [v3 新增] Mosaic 增强 (4 图拼接, 参考 YOLOv4)
    # ------------------------------------------------------------------
    def _mosaic_augment(self, img, seg):
        """
        随机取 3 个额外样本 (含当前图共 4 张),
        各缩至 H/2 x W/2, 拼成 2x2 大图.
        可显著增加多病害共现场景.
        """
        h, w = self.img_size
        h2, w2 = h // 2, w // 2

        imgs, segs = [], []
        # 当前样本缩放
        cur_img = Image.fromarray(img.astype(np.uint8)).resize((w2, h2), Image.BILINEAR)
        cur_seg = Image.fromarray(seg.astype(np.uint8)).resize((w2, h2), Image.NEAREST)
        imgs.append(np.array(cur_img, dtype=np.float32))
        segs.append(np.array(cur_seg, dtype=np.int64))

        # 额外 3 个随机样本
        for _ in range(3):
            other = random.choice(self.samples)
            try:
                o_img, o_mask = self._load_raw(other)
                o_class_id = other['class_id']
                o_seg = np.zeros(o_mask.shape, dtype=np.int64)
                o_seg[o_mask] = o_class_id
            except Exception:
                o_img = np.zeros((h2, w2, 3), dtype=np.float32)
                o_seg = np.zeros((h2, w2), dtype=np.int64)

            o_img_pil = Image.fromarray(o_img.astype(np.uint8)).resize((w2, h2), Image.BILINEAR)
            o_seg_pil = Image.fromarray(o_seg.astype(np.uint8)).resize((w2, h2), Image.NEAREST)
            imgs.append(np.array(o_img_pil, dtype=np.float32))
            segs.append(np.array(o_seg_pil, dtype=np.int64))

        # 拼接: [0 1 / 2 3]
        top = np.concatenate([imgs[0], imgs[1]], axis=1)
        bot = np.concatenate([imgs[2], imgs[3]], axis=1)
        mosaic_img = np.concatenate([top, bot], axis=0)

        top_s = np.concatenate([segs[0], segs[1]], axis=1)
        bot_s = np.concatenate([segs[2], segs[3]], axis=1)
        mosaic_seg = np.concatenate([top_s, bot_s], axis=0)

        return mosaic_img.astype(np.float32), mosaic_seg.astype(np.int64)

    # ------------------------------------------------------------------
    # 核心 __getitem__
    # ------------------------------------------------------------------
    def __getitem__(self, index):
        sample = self.samples[index]

        img, mask_bin = self._load_raw(sample)
        class_id = sample['class_id']
        seg = np.zeros(mask_bin.shape, dtype=np.int64)
        seg[mask_bin] = class_id

        # ---- 互斥的"重型"增强: Mosaic 与 Copy-Paste 二选一 ----
        # 原因: Mosaic 把图缩到 H/2, 1-3 px 裂缝在缩放后基本消失,
        # 这时再贴 Copy-Paste 反而把已经损坏的裂缝标签污染得更厉害.
        # 二选一即可获得多样性, 避免叠加污染.
        used_heavy_aug = False
        if self.augment and self.mosaic_prob > 0 and random.random() < self.mosaic_prob:
            img, seg = self._mosaic_augment(img, seg)
            used_heavy_aug = True

        if (self.augment and not used_heavy_aug
                and self.crack_copy_paste_prob > 0
                and random.random() < self.crack_copy_paste_prob):
            img, seg = self._crack_copy_paste(img, seg)

        # 裂缝 mask 膨胀 (轻量, 始终启用)
        if self.crack_dilate > 0:
            crack = (seg == 1)
            if crack.any():
                dilated = ndimage.binary_dilation(crack, iterations=self.crack_dilate)
                seg[dilated & (seg == 0)] = 1

        # 裂缝弹性变形 (轻量, 与上面互斥不冲突, 因为只局部弯曲)
        if (self.augment and self.crack_elastic_prob > 0
                and (seg == 1).any()
                and random.random() < self.crack_elastic_prob):
            try:
                img, seg = self._elastic_transform(img, seg)
            except Exception:
                pass

        # 通用几何/光度增强
        if self.augment:
            img, seg = self._augment(img, seg)

        # 归一化
        img = img / 255.0
        img = (img - IMG_MEAN) / IMG_STD

        img = torch.from_numpy(np.ascontiguousarray(img)).permute(2, 0, 1).float()
        seg = torch.from_numpy(np.ascontiguousarray(seg)).long()
        return img, seg

    def _augment(self, img, mask):
        # 水平 / 垂直翻转
        if random.random() < 0.5:
            img = np.flip(img, axis=1); mask = np.flip(mask, axis=1)
        if random.random() < 0.5:
            img = np.flip(img, axis=0); mask = np.flip(mask, axis=0)
        # 90 度旋转
        if random.random() < 0.5:
            k = random.randint(1, 3)
            img = np.rot90(img, k); mask = np.rot90(mask, k)
        # 随机缩放裁剪
        if random.random() < 0.5:
            img, mask = self._random_scale_crop(img, mask)
        # 亮度
        if random.random() < 0.5:
            img = np.clip(img * random.uniform(0.7, 1.3), 0, 255)
        # 对比度
        if random.random() < 0.5:
            m = img.mean()
            img = np.clip((img - m) * random.uniform(0.75, 1.3) + m, 0, 255)
        # [v3] 色彩抖动 (随机调整 R/G/B 各通道)
        if random.random() < 0.3:
            for c in range(3):
                img[:, :, c] = np.clip(img[:, :, c] * random.uniform(0.8, 1.2), 0, 255)
        # 高斯噪声
        if random.random() < 0.3:
            noise = np.random.normal(0, random.uniform(3, 10), img.shape)
            img = np.clip(img + noise, 0, 255)
        return img.astype(np.float32), mask.astype(np.int64)

    def _random_scale_crop(self, img, mask):
        h, w = img.shape[:2]
        scale = random.uniform(0.75, 1.25)          # v3: 扩大缩放范围
        nh, nw = int(h * scale), int(w * scale)

        img_pil = Image.fromarray(img.astype(np.uint8)).resize((nw, nh), Image.BILINEAR)
        mask_pil = Image.fromarray(mask.astype(np.uint8)).resize((nw, nh), Image.NEAREST)
        img_r = np.array(img_pil, dtype=np.float32)
        mask_r = np.array(mask_pil, dtype=np.int64)

        if nh >= h and nw >= w:
            y = random.randint(0, nh - h)
            x = random.randint(0, nw - w)
            img_r = img_r[y:y + h, x:x + w]
            mask_r = mask_r[y:y + h, x:x + w]
        else:
            pad_h = max(0, h - nh)
            pad_w = max(0, w - nw)
            img_r = np.pad(img_r, ((0, pad_h), (0, pad_w), (0, 0)), mode='reflect')[:h, :w]
            mask_r = np.pad(mask_r, ((0, pad_h), (0, pad_w)),
                            mode='constant', constant_values=0)[:h, :w]
        return img_r, mask_r
