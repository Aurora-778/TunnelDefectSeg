"""
util/loader_npy.py
==================================================
直接读 .npy 打包数据的 Dataset.

数据格式:
  data/npy/data_train.npy  — (N, 512, 512, 3) float32 [0, 255]
  data/npy/mask_train.npy  — (N, 512, 512)     int64   [0, 1]
  (val / test 同理)

__getitem__ 返回 (img_tensor, mask_tensor)
  img_tensor: (3, H, W) float32, ImageNet 归一化
  mask_tensor: (H, W) int64
"""

import os
import random
import numpy as np
from PIL import Image

import torch
from torch.utils.data import Dataset

IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMG_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class NpyDataset(Dataset):
    def __init__(self, npy_dir, split='train', img_size=(384, 384), augment=False):
        super().__init__()
        assert split in ('train', 'val', 'test')
        self.split = split
        self.img_size = img_size
        self.augment = augment

        data_path = os.path.join(npy_dir, f'data_{split}.npy')
        mask_path = os.path.join(npy_dir, f'mask_{split}.npy')

        if not os.path.exists(data_path):
            raise FileNotFoundError(f'找不到 {data_path}')
        if not os.path.exists(mask_path):
            raise FileNotFoundError(f'找不到 {mask_path}')

        self.images = np.load(data_path)   # (N, 512, 512, 3) float32
        self.masks = np.load(mask_path)     # (N, 512, 512)    int64

        assert len(self.images) == len(self.masks), \
            f'图片数 {len(self.images)} != mask 数 {len(self.masks)}'

        print(f'[{split.upper()}] 加载 {len(self.images)} 张, '
              f'图像 {self.images.shape}, mask {self.masks.shape}')

        # 兼容 test.py full-res 评估
        self.samples = []
        for i in range(len(self.images)):
            self.samples.append({
                'index': i,
                'img_path': f'{split}_{i}',
                'mask_path': f'{split}_{i}_mask',
                'class_id': 1,
                'folder': 'crack',
                '_npy_images': self.images,   # 引用, 不复制
                '_npy_masks': self.masks,
            })

    def __len__(self):
        return len(self.images)

    def update_input_size(self, h, w):
        self.img_size = (int(h), int(w))

    def __getitem__(self, index):
        img = self.images[index].copy()    # (512, 512, 3) float32
        mask = self.masks[index].copy()     # (512, 512) int64
        img_uint8 = img.astype(np.uint8)

        if self.augment:
            img_uint8, mask = self._augment(img_uint8, mask)

        h, w = self.img_size
        if img_uint8.shape[0] != h or img_uint8.shape[1] != w:
            img_pil = Image.fromarray(img_uint8)
            mask_pil = Image.fromarray(mask.astype(np.uint8))
            img_pil = img_pil.resize((w, h), Image.BILINEAR)
            mask_pil = mask_pil.resize((w, h), Image.NEAREST)
            img_uint8 = np.array(img_pil)
            mask = np.array(mask_pil).astype(np.int64)

        img_float = img_uint8.astype(np.float32) / 255.0
        img_float = (img_float - IMG_MEAN) / IMG_STD

        img_tensor = torch.from_numpy(img_float).permute(2, 0, 1).float()
        mask_tensor = torch.from_numpy(mask).long()
        return img_tensor, mask_tensor

    def _augment(self, img, mask):
        # 1. 随机缩放裁剪 (最重要: 让模型看到不同粗细的裂缝)
        if random.random() > 0.3:
            img, mask = self._random_scale_crop(img, mask)

        # 2. 翻转
        if random.random() > 0.5:
            img = np.fliplr(img).copy()
            mask = np.fliplr(mask).copy()
        if random.random() > 0.5:
            img = np.flipud(img).copy()
            mask = np.flipud(mask).copy()

        # 3. 90° 旋转
        k = random.randint(0, 3)
        if k > 0:
            img = np.rot90(img, k).copy()
            mask = np.rot90(mask, k).copy()

        # 4. 弹性变形 (让裂缝弯成新形状)
        if random.random() > 0.5:
            img, mask = self._elastic_deform(img, mask)

        # 5. 光度扰动 (更强)
        if random.random() > 0.3:
            img = self._color_jitter(img)

        # 6. 高斯噪声
        if random.random() > 0.6:
            sigma = random.uniform(5, 15)
            noise = np.random.normal(0, sigma, img.shape).astype(np.float32)
            img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        # 7. 随机遮挡 (让模型学会从局部推断)
        if random.random() > 0.7:
            img = self._random_erasing(img)

        return img, mask

    @staticmethod
    def _random_scale_crop(img, mask):
        """随机缩放 0.75–1.25 倍后裁回原尺寸"""
        h, w = img.shape[:2]
        scale = random.uniform(0.75, 1.25)
        new_h, new_w = int(h * scale), int(w * scale)

        img_pil = Image.fromarray(img).resize((new_w, new_h), Image.BILINEAR)
        mask_pil = Image.fromarray(mask.astype(np.uint8)).resize((new_w, new_h), Image.NEAREST)
        img_s = np.array(img_pil)
        mask_s = np.array(mask_pil).astype(np.int64)

        # 裁回原尺寸
        if new_h >= h and new_w >= w:
            y0 = random.randint(0, new_h - h)
            x0 = random.randint(0, new_w - w)
            img_s = img_s[y0:y0+h, x0:x0+w]
            mask_s = mask_s[y0:y0+h, x0:x0+w]
        else:
            # 缩小了, pad 回去
            pad_h = max(0, h - new_h)
            pad_w = max(0, w - new_w)
            img_s = np.pad(img_s, ((0, pad_h), (0, pad_w), (0, 0)), mode='reflect')
            mask_s = np.pad(mask_s, ((0, pad_h), (0, pad_w)), mode='constant', constant_values=0)
            img_s = img_s[:h, :w]
            mask_s = mask_s[:h, :w]

        return img_s, mask_s

    @staticmethod
    def _elastic_deform(img, mask, alpha=20, sigma=4):
        """轻量弹性变形: 让裂缝弯曲成不同形状"""
        from scipy.ndimage import gaussian_filter, map_coordinates
        h, w = img.shape[:2]
        dx = gaussian_filter(np.random.randn(h, w) * alpha, sigma)
        dy = gaussian_filter(np.random.randn(h, w) * alpha, sigma)
        y, x = np.meshgrid(np.arange(h), np.arange(w), indexing='ij')
        coords_y = np.clip(y + dy, 0, h - 1)
        coords_x = np.clip(x + dx, 0, w - 1)

        # 图像: 双线性插值
        out_img = np.zeros_like(img)
        for c in range(3):
            out_img[:, :, c] = map_coordinates(
                img[:, :, c], [coords_y, coords_x], order=1, mode='reflect'
            ).astype(np.uint8)

        # mask: 最近邻插值
        out_mask = map_coordinates(
            mask, [coords_y, coords_x], order=0, mode='constant', cval=0
        ).astype(np.int64)

        return out_img, out_mask

    @staticmethod
    def _random_erasing(img):
        """随机擦除一小块, 逼模型从上下文推断"""
        h, w = img.shape[:2]
        area = random.uniform(0.02, 0.08) * h * w
        aspect = random.uniform(0.5, 2.0)
        eh = int(min(h * 0.3, (area * aspect) ** 0.5))
        ew = int(min(w * 0.3, (area / aspect) ** 0.5))
        if eh > 0 and ew > 0:
            y0 = random.randint(0, h - eh)
            x0 = random.randint(0, w - ew)
            img[y0:y0+eh, x0:x0+ew] = np.random.randint(0, 255, (eh, ew, 3), dtype=np.uint8)
        return img

    @staticmethod
    def _color_jitter(img):
        """更强的光度扰动"""
        img = img.astype(np.float32)

        # 亮度 ±40
        img = np.clip(img + random.uniform(-40, 40), 0, 255)

        # 对比度 0.6–1.4
        contrast = random.uniform(0.6, 1.4)
        mean = img.mean()
        img = np.clip((img - mean) * contrast + mean, 0, 255)

        # gamma 变换 (对低对比度裂缝特别有效)
        if random.random() > 0.5:
            gamma = random.uniform(0.7, 1.5)
            img = np.clip(255.0 * (img / 255.0) ** gamma, 0, 255)

        # 通道随机偏移
        if random.random() > 0.7:
            for c in range(3):
                img[:, :, c] = np.clip(img[:, :, c] + random.uniform(-15, 15), 0, 255)

        return img.astype(np.uint8)
