"""快速验证脚本（无 conda 依赖）"""
import os, sys, torch
os.chdir(r'C:\Users\26822\Downloads\data')

# 1. 测试模型构造
print("[1] 测试模型构造...")
from train_resnet50 import ResNet50SegmentationModel
model = ResNet50SegmentationModel(
    num_classes=6,
    pretrained_path=r'C:\Users\26822\Downloads\resnet50_caffe-788b5fa3.pth'
)
print(f"    模型创建 OK")

# 2. GPU 测试
print("[2] 测试 GPU 推理...")
model = model.cuda()
x = torch.randn(2, 3, 384, 384).cuda()
with torch.no_grad():
    y = model(x)
print(f"    输出 shape: {y.shape}  (应为 [2, 6, 384, 384])")

# 3. 参数统计
total = sum(p.numel() for p in model.parameters())
print(f"    参数量: {total/1e6:.2f} M")

# 4. 数据集检查
print("[3] 检查数据集...")
imgs = [g for d in ['1','2','3','4','5']
        for g in sorted(os.listdir(os.path.join(d, 'images')))
        if g.lower().endswith(('.jpg','.jpeg','.png'))]
print(f"    图像数量: {len(imgs)} 张")
masks = [g for d in ['1','2','3','4','5']
         for g in sorted(os.listdir(os.path.join(d, 'labels')))
         if '_mask' in g]
print(f"    Mask 数量: {len(masks)} 对")
if len(imgs) > 0:
    print(f"    样例: {imgs[0]}")

print("\n=== 全部验证通过，可以开始训练 ===")
