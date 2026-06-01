r"""
binary_to_multiclass.py  —  OpenCV 极速版
将二值 mask (0/255) 拆分为 6 类:
  0=背景  1=simple  2=blocky  3=pipeline  4=vertical  5=horizontal

策略:
  - 连通域分析 + 方向梯度(Sobel) + 区域形状特征
  - 不用骨架化，纯向操作，速度极快
"""
import os
import cv2
import numpy as np
from PIL import Image
import glob

ROOT = r"C:\Users\26822\Downloads\data"
CLASS_NAMES = {1:"simple", 2:"blocky", 3:"pipeline", 4:"vertical", 5:"horizontal"}

def convert_mask(mask_np):
    """二值mask → 6类mask，O(1)每个连通域."""
    binary = (mask_np > 128).astype(np.uint8)
    multi = np.zeros_like(binary, dtype=np.int32)

    # OpenCV 连通域标记 (8连通)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num_labels <= 1:
        return multi  # 全黑

    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 50:
            continue  # 太小的区域 → 背景

        # 掩码
        region_mask = (labels == i).astype(np.uint8)

        # Sobel 方向能量 (仅在该连通域内)
        sobel_v = cv2.Sobel(region_mask, cv2.CV_32F, 0, 1, ksize=3)
        sobel_h = cv2.Sobel(region_mask, cv2.CV_32F, 1, 0, ksize=3)
        v_e = np.abs(sobel_v).sum()
        h_e = np.abs(sobel_h).sum()
        total_e = v_e + h_e + 1e-9
        v_ratio = v_e / total_e
        h_ratio = h_e / total_e

        # 最小外接矩形 → 长宽比
        pts = np.argwhere(region_mask == 1)
        if len(pts) == 0:
            continue
        rect = cv2.minAreaRect(pts[:, ::-1])  # (cx,cy),(w,h),angle
        _, (rw, rh), _ = rect
        long_side = max(rw, rh)
        short_side = min(rw, rh) + 1e-9
        aspect = long_side / short_side

        # 紧凑度: area / bounding_rect_area
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        bbox_area = w * h + 1e-9
        compactness = area / bbox_area

        # 形状判断
        if aspect > 3.5 and v_ratio > 0.65:
            cls = 4      # vertical crack (竖向裂纹)
        elif aspect > 3.5 and h_ratio > 0.65:
            cls = 5      # horizontal crack (横向裂纹)
        elif aspect > 2.5 and compactness < 0.35:
            cls = 3      # pipeline (管道状/细长区域)
        elif area > 8000 and compactness > 0.55:
            cls = 2      # blocky (大块、紧凑)
        elif area < 2000 and aspect < 2.0:
            cls = 1      # simple (小斑点)
        elif aspect > 2.5:
            cls = 3      # pipeline (中等细长)
        elif compactness < 0.4:
            cls = 2      # blocky (不规则大块)
        else:
            cls = 1      # simple (默认小块)

        multi[region_mask == 1] = cls

    return multi

def convert_all():
    output_dir = os.path.join(ROOT, "multiclass_labels")
    os.makedirs(output_dir, exist_ok=True)

    class_totals = {c: 0 for c in range(1, 6)}
    img_totals = {c: 0 for c in range(1, 6)}
    processed = 0

    for sub in ["1","2","3","4","5"]:
        lbl_dir = os.path.join(ROOT, sub, "labels")
        out_sub = os.path.join(output_dir, sub)
        os.makedirs(out_sub, exist_ok=True)

        masks = glob.glob(os.path.join(lbl_dir, "*_mask.png"))
        print(f"[{sub}] {len(masks)} masks")

        for mf in masks:
            mask = np.array(Image.open(mf).convert("L"))
            multi = convert_mask(mask)

            base = os.path.basename(mf).replace("_mask", "_multi")
            cv2.imwrite(os.path.join(out_sub, base), multi)

            for c in range(1, 6):
                cnt = int((multi == c).sum())
                class_totals[c] += cnt
                if cnt > 0:
                    img_totals[c] += 1

            processed += 1

    print()
    print("=" * 55)
    print("  多类拆分完成！")
    print("=" * 55)
    print(f"  处理图片: {processed} 张")
    print()
    print("  各类像素总量:")
    for c in range(1, 6):
        pct = 100 * class_totals[c] / max(sum(class_totals.values()), 1)
        bar = "█" * int(pct / 2)
        print(f"  [{c}] {CLASS_NAMES[c]:12s}: {class_totals[c]:12,d} px  ({pct:5.1f}%) {bar}")
    print()
    print("  包含该类的图片数:")
    for c in range(1, 6):
        print(f"  [{c}] {CLASS_NAMES[c]:12s}: {img_totals[c]:5d} 张  ({(img_totals[c]/processed*100):.1f}%)")
    print()
    print(f"  输出目录: {output_dir}")
    return output_dir

if __name__ == "__main__":
    convert_all()
