"""
encoder_mobilenetv3.py
=============================================
MobileNetV3-Small 编码器, 即插即用替换 TunnelDefectSeg 的自定义 IRB stack.

为什么这是小数据上最值得做的优化:
  * MobileNetV3-Small ImageNet 预训练权重在 torchvision 里现成可用
  * 参数量 ~2.5M, 与现有 Small 配置几乎一致
  * 小数据集裂缝任务 ImageNet 预训练通常稳定 +2~4 mIoU
  * 输入 384 时各 stage 分辨率 (192, 96, 48, 24, 12), 与现有 decoder 兼容

输出 5 个 feature map, 与现 model 的 (s0, s1, s2, s3, s4) 对应:
  stage   stride   channels   layers in torchvision MobileNetV3-Small
  s0 stem   2          16        features[0]
  s1        4          16        features[1]
  s2        8          24        features[2..3]
  s3       16          48        features[4..8]
  s4       32          96        features[9..11]   ← 注意是 stride 32, 比原 model 的 s4 stride 16 多一次下采样

用法:
  from models.encoder_mobilenetv3 import MobileNetV3SmallEncoder
  enc = MobileNetV3SmallEncoder(pretrained=True)
  feats = enc(x)   # [s0, s1, s2, s3, s4]

注意: 由于 s4 是 stride 32, ASPP 的空洞率应缩小 (建议 (2, 4, 6) 而不是 (6,12,18))
"""
import torch
import torch.nn as nn


# torchvision 的 MobileNetV3 是必需依赖, 训练 requirements 已含 torchvision
try:
    from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
    _TV_AVAILABLE = True
except Exception:
    _TV_AVAILABLE = False


# 5 stage 在 torchvision MobileNet_V3-Small features 中的切片边界
# 在每个 (start, end) 对的 end 输出处取 feature
_MBV3S_SPLITS = [
    (0, 1),   # s0: stride 2,   ch 16
    (1, 2),   # s1: stride 4,   ch 16
    (2, 4),   # s2: stride 8,   ch 24
    (4, 9),   # s3: stride 16,  ch 48
    (9, 12),  # s4: stride 32,  ch 96
]
MBV3_SMALL_CHANNELS = (16, 16, 24, 48, 96)


class MobileNetV3SmallEncoder(nn.Module):
    """
    MobileNetV3-Small 编码器.

    Args:
        pretrained:  是否加载 ImageNet 预训练权重 (强烈建议 True)
        frozen_bn:   是否把 BN 设为 eval 模式 + freeze 参数
                     (小 batch + 小数据时可能稳定一点; 默认 False)
    """
    def __init__(self, pretrained=True, frozen_bn=False):
        super().__init__()
        if not _TV_AVAILABLE:
            raise ImportError(
                "torchvision 不可用。MobileNetV3 编码器需要 torchvision >= 0.13。"
            )

        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
        full = mobilenet_v3_small(weights=weights)

        # 只保留 features, 拆成 5 段 ModuleList
        feats = full.features
        stages = []
        for s, e in _MBV3S_SPLITS:
            stages.append(nn.Sequential(*[feats[i] for i in range(s, e)]))
        self.stages = nn.ModuleList(stages)
        self.out_channels = MBV3_SMALL_CHANNELS

        if frozen_bn:
            self._freeze_bn()

    def _freeze_bn(self):
        for m in self.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()
                for p in m.parameters():
                    p.requires_grad = False

    def forward(self, x):
        feats = []
        for stage in self.stages:
            x = stage(x)
            feats.append(x)
        return feats  # [s0, s1, s2, s3, s4]


# ============================================================
# 自检
# ============================================================
if __name__ == '__main__':
    if not _TV_AVAILABLE:
        print('torchvision 不可用, 跳过')
    else:
        enc = MobileNetV3SmallEncoder(pretrained=False)
        x = torch.randn(2, 3, 384, 384)
        feats = enc(x)
        for i, f in enumerate(feats):
            print(f's{i}: {tuple(f.shape)}')
        # 期望:
        # s0: (2, 16, 192, 192)
        # s1: (2, 16, 96, 96)
        # s2: (2, 24, 48, 48)
        # s3: (2, 48, 24, 24)
        # s4: (2, 96, 12, 12)
