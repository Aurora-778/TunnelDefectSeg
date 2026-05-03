"""
隧道裂缝分割 - 类别定义 (2 类: 背景 + 裂缝)
================================================
适配 .npy 数据集: mask 中 0=背景, 1=裂缝
"""

import numpy as np

NUM_CLASSES = 2  # 0=背景, 1=裂缝

FOLDER_TO_CLASS = {'crack': 1}
CLASS_TO_FOLDER = {v: k for k, v in FOLDER_TO_CLASS.items()}

CLASS_NAMES = {
    0: '背景',
    1: '裂缝',
}

CLASS_COLORS = {
    0: (0,   0,   0),     # 背景 - 黑色
    1: (255, 0,   0),     # 裂缝 - 红色
}

CRACK_CLASSES   = [1]
LEAKAGE_CLASSES = []
DAMAGE_CLASSES  = []

GROUP_NAMES = {'裂缝': CRACK_CLASSES}

def get_color_palette():
    palette = np.zeros((NUM_CLASSES, 3), dtype=np.uint8)
    for cls_id, color in CLASS_COLORS.items():
        palette[cls_id] = color
    return palette

def mask_to_color(mask):
    palette = get_color_palette()
    h, w = mask.shape
    color_img = np.zeros((h, w, 3), dtype=np.uint8)
    for cls_id in range(NUM_CLASSES):
        color_img[mask == cls_id] = palette[cls_id]
    return color_img
