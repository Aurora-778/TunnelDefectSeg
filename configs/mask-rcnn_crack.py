# ─────────────────────────────────────────────────────────────────────────────
# Mask R-CNN (ResNet-50 + FPN) — 裂缝实例分割配置
#
# 继承官方 COCO 1x 配置，仅修改：
#   - num_classes = 1（crack）
#   - data_root / ann_file / data_prefix
#   - batch size / workers（根据 RTX 3060 Laptop 6GB 调整）
#   - max_epochs = 12（1x schedule）
#
# 依赖：mmdetection 已安装，mmcv 已编译
# 训练命令：
#   cd <mmdetection 目录>
#   python tools/train.py C:\Users\26822\Downloads\data\configs\mask-rcnn_crack.py
# ─────────────────────────────────────────────────────────────────────────────

_base_ = [
    "mmdet::mask_rcnn/mask-rcnn_r50-caffe_fpn_ms-poly-1x_coco.py"
]

# ───────── 数据集元信息 ──────────────────────────────────────────────────────
# 覆盖基类的 COCO 80 类，改为单类裂缝检测
metainfo = dict(
    classes=("crack",),
    palette=[(220, 20, 60)]       # 可视化颜色（红色）
)

# ───────── 数据集路径 ─────────────────────────────────────────────────────────
data_root = r"C:\Users\26822\Downloads\data"

# ───────── 模型：修改 num_classes ─────────────────────────────────────────────
model = dict(
    roi_head=dict(
        bbox_head=dict(num_classes=1),
        mask_head=dict(num_classes=1)
    )
)

# ───────── 数据流水线 ─────────────────────────────────────────────────────────
# 使用基类默认的增强策略（ms-poly），此处只覆盖 dataloader 参数
train_dataloader = dict(
    batch_size=2,            # RTX 3060 6GB，图像 1315×986 时 bs=2 较安全
    num_workers=2,
    dataset=dict(
        type="CocoDataset",
        metainfo=metainfo,
        data_root=data_root,
        ann_file="annotations/train.json",
        data_prefix=dict(img=""),   # file_name 已含 "1/images/xxx.jpg" 相对路径
    )
)

val_dataloader = dict(
    batch_size=1,
    num_workers=2,
    dataset=dict(
        type="CocoDataset",
        metainfo=metainfo,
        data_root=data_root,
        ann_file="annotations/val.json",
        data_prefix=dict(img=""),
    )
)

test_dataloader = val_dataloader

val_evaluator = dict(
    type="CocoMetric",
    ann_file=data_root + r"\annotations\val.json",
    metric=["bbox", "segm"],
    format_only=False
)
test_evaluator = val_evaluator

# ───────── 训练策略 ───────────────────────────────────────────────────────────
# 1x schedule：12 epoch，第 8、11 epoch 降 lr
max_epochs = 12
train_cfg  = dict(type="EpochBasedTrainLoop", max_epochs=max_epochs, val_interval=1)

param_scheduler = [
    dict(
        type="LinearLR",
        start_factor=0.001,
        by_epoch=False,
        begin=0,
        end=500           # warmup 500 iter
    ),
    dict(
        type="MultiStepLR",
        begin=0,
        end=max_epochs,
        by_epoch=True,
        milestones=[8, 11],
        gamma=0.1
    )
]

# ───────── 优化器 ─────────────────────────────────────────────────────────────
optim_wrapper = dict(
    type="OptimWrapper",
    optimizer=dict(
        type="SGD",
        lr=0.005,         # 原版 0.02 对应 bs=16；bs=2 → 0.02*(2/16)=0.0025
                          # 取略大值 0.005 避免收敛过慢
        momentum=0.9,
        weight_decay=0.0001
    ),
    clip_grad=dict(max_norm=35, norm_type=2)
)

# ───────── 日志 & 检查点 ──────────────────────────────────────────────────────
default_hooks = dict(
    checkpoint=dict(
        type="CheckpointHook",
        interval=1,
        max_keep_ckpts=3,
        save_best="segm_mAP"    # 按分割 mAP 保存最优权重
    ),
    logger=dict(type="LoggerHook", interval=50)
)

# ───────── 工作目录 ───────────────────────────────────────────────────────────
work_dir = r"C:\Users\26822\Downloads\data\work_dirs\mask-rcnn_crack"
