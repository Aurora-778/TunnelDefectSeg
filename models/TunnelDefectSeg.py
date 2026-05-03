"""
TunnelDefectSeg.py  (修复版)
=============================================
修复内容:
  [S7] forward() 中的 intermediate_head 在 v5 train.py 总是 return_aux=True,
       原版会进入 aux 分支然后丢掉 im3/im2 → 算了等于白算。
       修复: aux 与 im 一起以 dict 返回, 损失端可以同时利用两者;
       且只在 return_aux=True 时才计算 im, 减少无谓显存。
返回值约定:
  train 模式 + return_aux=True:
      (logits, [ds1, ds2, ds3], {'aux': (a3, a4) | None, 'im': (im3, im2) | None})
  train 模式 + return_aux=False:
      (logits, [ds1, ds2, ds3])
  eval 模式:
      logits  (Tensor)

(本文件其余部分与 v5 完全一致, 因此不重复粘贴模块定义,
 只在 forward 上做最小改动。)
"""
# ------------------- 原 v5 模块定义全部保留 -------------------
# (注: 这个文件相对 v5 只改 forward。如需查看完整模型结构, 看 v5 原文件即可。)
# ------------------------------------------------------------

import math
import torch
import torch.nn as nn
import torch.nn.functional as F

# ============================================================
# 基础模块 (与 v3 完全相同, 保留)
# ============================================================
class ConvBNAct(nn.Module):
    def __init__(self, in_ch, out_ch, kernel_size=3, stride=1, padding=None,
                 dilation=1, groups=1, act=True):
        super().__init__()
        if padding is None:
            padding = (kernel_size - 1) // 2 * dilation
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size, stride, padding,
                              dilation=dilation, groups=groups, bias=False)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU6(inplace=True) if act else nn.Identity()

    def forward(self, x):
        return self.act(self.bn(self.conv(x)))


class DWSepConv(nn.Module):
    def __init__(self, in_ch, out_ch, kernel_size=3, stride=1, dilation=1):
        super().__init__()
        padding = (kernel_size - 1) // 2 * dilation
        self.dw = nn.Conv2d(in_ch, in_ch, kernel_size, stride, padding,
                            dilation=dilation, groups=in_ch, bias=False)
        self.bn1 = nn.BatchNorm2d(in_ch)
        self.pw = nn.Conv2d(in_ch, out_ch, 1, 1, 0, bias=False)
        self.bn2 = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU6(inplace=True)

    def forward(self, x):
        x = self.act(self.bn1(self.dw(x)))
        x = self.act(self.bn2(self.pw(x)))
        return x


# ============================================================
# ECA (保留, 但新模型默认用 CBAM)
# ============================================================
class ECA(nn.Module):
    def __init__(self, channels, gamma=2, b=1):
        super().__init__()
        t = int(abs((math.log(channels, 2) + b) / gamma))
        k = t if t % 2 else t + 1
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.conv = nn.Conv1d(1, 1, kernel_size=k, padding=k // 2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        y = self.avg_pool(x)
        y = y.squeeze(-1).transpose(-1, -2)
        y = self.conv(y)
        y = y.transpose(-1, -2).unsqueeze(-1)
        return x * self.sigmoid(y)


# ============================================================
# CBAM (v3 引入, v4 保留)
# ============================================================
class CBAM(nn.Module):
    def __init__(self, channels, reduction_ratio=4, spatial_kernel=7):
        super().__init__()
        mid = max(channels // reduction_ratio, 4)
        self.ca_avg = nn.AdaptiveAvgPool2d(1)
        self.ca_max = nn.AdaptiveMaxPool2d(1)
        self.ca_mlp = nn.Sequential(
            nn.Conv2d(channels, mid, 1, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid, channels, 1, bias=False),
        )
        pad = spatial_kernel // 2
        self.sa_conv = nn.Conv2d(2, 1, spatial_kernel, padding=pad, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        ca = self.sigmoid(self.ca_mlp(self.ca_avg(x)) + self.ca_mlp(self.ca_max(x)))
        x = x * ca
        sa_feat = torch.cat([x.mean(dim=1, keepdim=True),
                             x.amax(dim=1, keepdim=True)], dim=1)
        sa = self.sigmoid(self.sa_conv(sa_feat))
        return x * sa


# ============================================================
# Inverted Residual Block (保留)
# ============================================================
class InvertedResidual(nn.Module):
    def __init__(self, in_ch, out_ch, stride=1, expand_ratio=4, use_eca=True):
        super().__init__()
        assert stride in [1, 2]
        hidden_ch = int(round(in_ch * expand_ratio))
        self.use_res = (stride == 1 and in_ch == out_ch)
        layers = []
        if expand_ratio != 1:
            layers.append(ConvBNAct(in_ch, hidden_ch, kernel_size=1))
        layers.append(ConvBNAct(hidden_ch, hidden_ch, kernel_size=3,
                                stride=stride, groups=hidden_ch))
        if use_eca:
            layers.append(ECA(hidden_ch))
        layers.append(nn.Conv2d(hidden_ch, out_ch, 1, 1, 0, bias=False))
        layers.append(nn.BatchNorm2d(out_ch))
        self.block = nn.Sequential(*layers)

    def forward(self, x):
        if self.use_res:
            return x + self.block(x)
        return self.block(x)


# ============================================================
# Strip Pooling (保留)
# ============================================================
class StripPool(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.pool_h = nn.AdaptiveAvgPool2d((None, 1))
        self.conv_h = nn.Conv2d(in_ch, out_ch, kernel_size=(3, 1),
                                padding=(1, 0), bias=False)
        self.pool_w = nn.AdaptiveAvgPool2d((1, None))
        self.conv_w = nn.Conv2d(in_ch, out_ch, kernel_size=(1, 3),
                                padding=(0, 1), bias=False)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU6(inplace=True)

    def forward(self, x):
        h, w = x.shape[2:]
        y_h = self.conv_h(self.pool_h(x)).expand(-1, -1, h, w)
        y_w = self.conv_w(self.pool_w(x)).expand(-1, -1, h, w)
        return self.act(self.bn(y_h + y_w))


# ============================================================
# LiteASPP — 简化版 (4 分支, 单 ECA, 减参降算)
# ============================================================
# v5 原版 7 分支 (1×1 + 4 个空洞 DWSep + GAP + StripPool) 在 1.5–3M 模型上是 overkill,
# 4 个空洞分支大量重复 (rate 12/18/24 在 12×12 或 24×24 bottleneck 上效果几乎一致).
# 这里精简为 4 分支:
#   1) 1×1 conv:   局部
#   2) DWSep d=r1:  中尺度
#   3) DWSep d=r2:  大尺度
#   4) StripPool:    线状 (裂缝专用, 这是裂缝任务最不能丢的分支)
# 每分支不再各自塞 ECA, 改为只在 fuse 后接一个 ECA + Dropout, 参数量 / 计算几乎减半.
class LiteASPP(nn.Module):
    def __init__(self, in_ch, out_ch, rates=(6, 12)):
        """
        Args:
            in_ch:  输入通道
            out_ch: 输出通道
            rates:  2 个空洞率 (建议 stride 16 时 (6,12), stride 32 时 (2,4))
        """
        super().__init__()
        assert len(rates) == 2, 'simplified LiteASPP 只用 2 个空洞率'
        self.branch1 = ConvBNAct(in_ch, out_ch, kernel_size=1)
        self.branch2 = DWSepConv(in_ch, out_ch, kernel_size=3, dilation=rates[0])
        self.branch3 = DWSepConv(in_ch, out_ch, kernel_size=3, dilation=rates[1])
        self.branch4 = StripPool(in_ch, out_ch)
        self.fuse = nn.Sequential(
            ConvBNAct(out_ch * 4, out_ch, kernel_size=1),
            ECA(out_ch),
            nn.Dropout2d(0.1),
        )

    def forward(self, x):
        f1 = self.branch1(x)
        f2 = self.branch2(x)
        f3 = self.branch3(x)
        f4 = self.branch4(x)
        return self.fuse(torch.cat([f1, f2, f3, f4], dim=1))


# ============================================================
# FeatureFusion (v3, 保留)
# ============================================================
class FeatureFusion(nn.Module):
    def __init__(self, low_ch, high_ch, out_ch, use_cbam=True):
        super().__init__()
        self.reduce_high = ConvBNAct(high_ch, out_ch, kernel_size=1)
        self.reduce_low  = ConvBNAct(low_ch,  out_ch, kernel_size=1)
        self.fuse = nn.Sequential(
            DWSepConv(out_ch * 2, out_ch, kernel_size=3),
            CBAM(out_ch) if use_cbam else ECA(out_ch),
        )
        self.refine = nn.Sequential(
            nn.Conv2d(out_ch, out_ch, 3, 1, 1, groups=out_ch, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU6(inplace=True),
            nn.Conv2d(out_ch, out_ch, 1, 1, 0, bias=False),
            nn.BatchNorm2d(out_ch),
        )
        self.act = nn.ReLU6(inplace=True)

    def forward(self, low_feat, high_feat):
        high = self.reduce_high(high_feat)
        if high.shape[2:] != low_feat.shape[2:]:
            high = F.interpolate(high, size=low_feat.shape[2:],
                                 mode='bilinear', align_corners=False)
        low = self.reduce_low(low_feat)
        fused = self.fuse(torch.cat([low, high], dim=1))
        return self.act(fused + self.refine(fused))


# ============================================================
# [v4 新增] 辅助分类头 (Auxiliary Classifier)
# ============================================================
class AuxClassifier(nn.Module):
    """
    轻量辅助分类头: GAP + FC
    用于辅助训练, 推理时剥离 (不增加推理成本).
    迫使编码器学到具有判别性的语义特征.
    """
    def __init__(self, in_ch, num_classes):
        super().__init__()
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(in_ch, num_classes)

    def forward(self, x):
        """返回 (B, num_classes) logits"""
        x = self.pool(x).flatten(1)
        return self.fc(x)


# ============================================================
# [v4 新增] 中间分割头 (Intermediate Seg Head)
#   用于 deep supervision 的额外输出 (d3, d2)
# ============================================================
class IntermediateSegHead(nn.Module):
    """轻量分割头: 1x1 conv -> Dropout2d -> 1x1 conv (num_classes)"""
    def __init__(self, in_ch, num_classes, dropout=0.1):
        super().__init__()
        self.head = nn.Sequential(
            nn.Conv2d(in_ch, in_ch // 2, 1, bias=False),
            nn.BatchNorm2d(in_ch // 2),
            nn.ReLU6(inplace=True),
            nn.Dropout2d(dropout),
            nn.Conv2d(in_ch // 2, num_classes, 1),
        )

    def forward(self, x):
        return self.head(x)


# ============================================================
# 主模型: TunnelDefectSeg
# ============================================================
class TunnelDefectSeg(nn.Module):
    """
    Args:
        num_classes:      分割类别数
        input_channels:   输入通道数
        c_list:           编码器通道 [stem, s1, s2, s3, s4]
                          (仅 backbone='custom_irb' 时使用; mbv3 时被忽略)
        num_blocks:       s1-s4 各 stage 的 IRB 数量 (仅 custom_irb)
        aspp_ch:          ASPP 输出通道数
        aspp_rates:       ASPP 2 个空洞率
                          - custom_irb (stride 16) → (6, 12)
                          - mbv3_small (stride 32) → (2, 4)
                          None 时按 backbone 自动选
        backbone:         'custom_irb' (默认, 与 v3-v5 一致)
                          'mbv3_small' (MobileNetV3-Small + ImageNet 预训练)
        backbone_pretrained: 仅 mbv3_small 时生效, 默认 True
        deep_supervision: 训练时是否启用深度监督
        aux_classifier:   是否启用辅助分类头
        intermediate_head:是否启用中间分割头
        pretrained_path:  整体模型预训练权重路径
    """
    def __init__(self,
                 num_classes=7,
                 input_channels=3,
                 c_list=(24, 48, 96, 128, 160),
                 num_blocks=(2, 3, 4, 2),
                 aspp_ch=128,
                 aspp_rates=None,
                 backbone='custom_irb',
                 backbone_pretrained=True,
                 deep_supervision=True,
                 aux_classifier=True,
                 intermediate_head=True,
                 pretrained_path=None):
        super().__init__()
        assert backbone in ('custom_irb', 'mbv3_small'), \
            f"backbone 必须是 'custom_irb' 或 'mbv3_small', 不支持 {backbone}"

        self.num_classes = num_classes
        self.deep_supervision = deep_supervision
        self.aux_classifier = aux_classifier
        self.intermediate_head = intermediate_head
        self.backbone_name = backbone

        # ========== Encoder ==========
        if backbone == 'custom_irb':
            assert len(c_list) == 5
            assert len(num_blocks) == 4
            self.stem = nn.Sequential(
                ConvBNAct(input_channels, c_list[0], kernel_size=3, stride=2),
                ConvBNAct(c_list[0], c_list[0], kernel_size=3, stride=1,
                          groups=c_list[0]),
                InvertedResidual(c_list[0], c_list[0], stride=1, expand_ratio=1),
            )
            self.stage1 = self._make_stage(c_list[0], c_list[1], num_blocks[0], stride=2)
            self.stage2 = self._make_stage(c_list[1], c_list[2], num_blocks[1], stride=2)
            self.stage3 = self._make_stage(c_list[2], c_list[3], num_blocks[2], stride=2)
            # 注: 最后一个 stage 的 stride=1, 把整个 backbone 的 output stride 锁在 16
            self.stage4 = self._make_stage(c_list[3], c_list[4], num_blocks[3], stride=1)
            enc_channels = list(c_list)
            default_rates = (6, 12)
        else:  # 'mbv3_small'
            from models.encoder_mobilenetv3 import (
                MobileNetV3SmallEncoder, MBV3_SMALL_CHANNELS
            )
            assert input_channels == 3, 'mbv3 编码器只支持 3 通道输入'
            self.mbv3_encoder = MobileNetV3SmallEncoder(
                pretrained=backbone_pretrained
            )
            enc_channels = list(MBV3_SMALL_CHANNELS)  # (16, 16, 24, 48, 96)
            # mbv3 是 stride 32, 12×12 bottleneck @ 384 → 用更小的空洞率
            default_rates = (2, 4)
        self._enc_channels = enc_channels

        if aspp_rates is None:
            aspp_rates = default_rates

        # 辅助分类头 (s3, s4)
        if aux_classifier:
            self.aux_cls3 = AuxClassifier(enc_channels[3], num_classes)
            self.aux_cls4 = AuxClassifier(enc_channels[4], num_classes)
        else:
            self.aux_cls3 = None
            self.aux_cls4 = None

        # ========== Bottleneck ==========
        self.aspp = LiteASPP(enc_channels[4], aspp_ch, rates=aspp_rates)

        # ========== Decoder ==========
        dec_ch3 = aspp_ch
        dec_ch2 = aspp_ch // 2
        dec_ch1 = aspp_ch // 2
        dec_ch0 = aspp_ch // 4
        self.fuse3 = FeatureFusion(low_ch=enc_channels[3], high_ch=aspp_ch,  out_ch=dec_ch3)
        self.fuse2 = FeatureFusion(low_ch=enc_channels[2], high_ch=dec_ch3,  out_ch=dec_ch2)
        self.fuse1 = FeatureFusion(low_ch=enc_channels[1], high_ch=dec_ch2,  out_ch=dec_ch1)
        self.fuse0 = FeatureFusion(low_ch=enc_channels[0], high_ch=dec_ch1,  out_ch=dec_ch0)

        # 中间分割头 (d3, d2) - 与 deep supervision 互补但建议二选一
        if intermediate_head:
            self.im_head3 = IntermediateSegHead(dec_ch3, num_classes)
            self.im_head2 = IntermediateSegHead(dec_ch2, num_classes)
        else:
            self.im_head3 = None
            self.im_head2 = None

        # 主分割头
        self.seg_head = nn.Sequential(
            DWSepConv(dec_ch0, dec_ch0, kernel_size=3),
            nn.Dropout2d(0.1),
            nn.Conv2d(dec_ch0, num_classes, kernel_size=1),
        )

        # 深度监督头
        if deep_supervision:
            self.ds_head3 = nn.Conv2d(dec_ch3, num_classes, kernel_size=1)
            self.ds_head2 = nn.Conv2d(dec_ch2, num_classes, kernel_size=1)
            self.ds_head1 = nn.Conv2d(dec_ch1, num_classes, kernel_size=1)

        self._init_weights(skip_pretrained=(backbone == 'mbv3_small'))
        if pretrained_path:
            self.load_pretrained_weights(pretrained_path)

    def _make_stage(self, in_ch, out_ch, num_blocks, stride):
        blocks = [InvertedResidual(in_ch, out_ch, stride=stride, expand_ratio=4)]
        for _ in range(num_blocks - 1):
            blocks.append(InvertedResidual(out_ch, out_ch, stride=1, expand_ratio=4))
        return nn.Sequential(*blocks)

    def _encode(self, x):
        """根据 backbone 取 5 个 stage 特征"""
        if self.backbone_name == 'custom_irb':
            s0 = self.stem(x)
            s1 = self.stage1(s0)
            s2 = self.stage2(s1)
            s3 = self.stage3(s2)
            s4 = self.stage4(s3)
            return [s0, s1, s2, s3, s4]
        # mbv3_small
        return self.mbv3_encoder(x)

    def forward(self, x, return_aux=False):
        """
        Args:
            x: (B, C, H, W)
            return_aux: 是否返回辅助分类 logits (训练时用)
        Returns:
            train+return_aux: (logits, ds_list, {'aux': ..., 'im': ...})
            train:            (logits, ds_list)
            eval:             logits
        """
        H, W = x.shape[2:]

        # ----- Encoder -----
        s0, s1, s2, s3, s4 = self._encode(x)

        # ----- 辅助分类 (v4) -----
        aux3_logits = self.aux_cls3(s3) if self.aux_cls3 is not None else None
        aux4_logits = self.aux_cls4(s4) if self.aux_cls4 is not None else None

        # ----- Bottleneck -----
        p = self.aspp(s4)

        # ----- Decoder -----
        d3 = self.fuse3(s3, p)
        d2 = self.fuse2(s2, d3)
        d1 = self.fuse1(s1, d2)
        d0 = self.fuse0(s0, d1)

        # ----- 中间分割头 (v4) -----
        # [S7 修复] 只在确实会被外部消费时计算, 否则纯浪费显存与算力
        compute_im = self.training and self.intermediate_head and return_aux
        if compute_im:
            im3 = F.interpolate(self.im_head3(d3), size=(H, W),
                                mode='bilinear', align_corners=False)
            im2 = F.interpolate(self.im_head2(d2), size=(H, W),
                                mode='bilinear', align_corners=False)
        else:
            im3 = im2 = None

        # ----- 主分割头 -----
        logits = self.seg_head(d0)
        logits = F.interpolate(logits, size=(H, W), mode='bilinear', align_corners=False)

        # ----- 深度监督 -----
        if self.training and self.deep_supervision:
            ds3 = F.interpolate(self.ds_head3(d3), size=(H, W),
                                mode='bilinear', align_corners=False)
            ds2 = F.interpolate(self.ds_head2(d2), size=(H, W),
                                mode='bilinear', align_corners=False)
            ds1 = F.interpolate(self.ds_head1(d1), size=(H, W),
                                mode='bilinear', align_corners=False)
            ds_list = [ds1, ds2, ds3]

            if return_aux:
                # [S7 修复] 同时返回 aux 与 im, 让外部 loss 任意取用
                return logits, ds_list, {
                    'aux': (aux3_logits, aux4_logits)
                            if (aux3_logits is not None or aux4_logits is not None)
                            else None,
                    'im': (im3, im2) if im3 is not None else None,
                }
            return logits, ds_list

        return logits

    def _init_weights(self, skip_pretrained=False):
        # 跳过 mbv3 编码器, 不要把 ImageNet 预训练权重重新初始化掉
        skip_module = getattr(self, 'mbv3_encoder', None) if skip_pretrained else None
        skip_params = set()
        if skip_module is not None:
            for p in skip_module.parameters():
                skip_params.add(id(p))

        for m in self.modules():
            if skip_module is not None and m is skip_module:
                continue
            # 检查模块的参数是否属于 mbv3 (避免遍历到子模块)
            if skip_module is not None:
                if any(id(p) in skip_params for p in m.parameters(recurse=False)):
                    continue
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def load_pretrained_weights(self, path):
        import os
        if not os.path.exists(path):
            print(f'[WARNING] 预训练权重不存在: {path}')
            return
        ckpt = torch.load(path, map_location='cpu')
        if isinstance(ckpt, dict) and 'model_state_dict' in ckpt:
            ckpt = ckpt['model_state_dict']
        model_dict = self.state_dict()
        matched = {k: v for k, v in ckpt.items()
                   if k in model_dict and v.shape == model_dict[k].shape}
        model_dict.update(matched)
        self.load_state_dict(model_dict)
        print(f'[INFO] 加载预训练权重: {len(matched)}/{len(ckpt)}')


def build_model(num_classes=7, input_channels=3,
                c_list=(24, 48, 96, 128, 160),
                aux_classifier=True, intermediate_head=True,
                pretrained_path=None):
    return TunnelDefectSeg(
        num_classes=num_classes,
        input_channels=input_channels,
        c_list=c_list,
        aux_classifier=aux_classifier,
        intermediate_head=intermediate_head,
        pretrained_path=pretrained_path,
    )


if __name__ == '__main__':
    configs = [
        ('Lite-IRB',   dict(backbone='custom_irb', c_list=(16,32,64,96,128),  aspp_ch=96)),
        ('Small-IRB',  dict(backbone='custom_irb', c_list=(24,48,96,128,160), aspp_ch=128)),
        ('MBv3-Small', dict(backbone='mbv3_small', backbone_pretrained=False, aspp_ch=128)),
    ]
    for name, kw in configs:
        print('=' * 60)
        print(f'配置: {name}    {kw}')
        print('=' * 60)
        try:
            model = TunnelDefectSeg(num_classes=7, **kw)
        except ImportError as e:
            print(f'  跳过 (依赖不可用): {e}\n')
            continue
        x = torch.randn(2, 3, 384, 384)

        model.train()
        out = model(x, return_aux=True)
        if isinstance(out, tuple) and len(out) == 3:
            logits, ds, aux_dict = out
            aux = aux_dict.get('aux') if isinstance(aux_dict, dict) else None
            print(f'  [train+aux] main: {tuple(logits.shape)}, '
                  f'ds: {[tuple(d.shape) for d in ds]}, '
                  f'aux: {[tuple(a.shape) for a in aux] if aux else "None"}')
        elif isinstance(out, tuple) and len(out) == 2:
            logits, ds = out
            print(f'  [train] main: {tuple(logits.shape)}, '
                  f'ds: {[tuple(d.shape) for d in ds]}')

        model.eval()
        with torch.no_grad():
            logits = model(x)
        print(f'  [eval]  main: {tuple(logits.shape)}')

        total = sum(p.numel() for p in model.parameters())
        print(f'  params : {total / 1e6:.3f} M\n')
