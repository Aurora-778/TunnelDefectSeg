"""
dcn.py  (修复版)
=============================================
修复内容:
  [B3] 删除第 75 行的乱码:
       self.dcn = torch.ops.ops.torch.ops.torch.ops.torch.ops.torch.ops
  - 修正 forward() 中对 torchvision.ops.deform_conv2d 的调用:
       该函数没有 deformable_groups= 关键字, 它从 offset 形状自动推断。
  - 在 __init__ 一次性把 deform_conv2d 函数 import 缓存成属性, 避免每次
    forward 都重新 import。
  - DCN 不可用时(老 torchvision)自动退化为标准卷积, 不影响训练。
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


def _try_import_deform_conv2d():
    try:
        from torchvision.ops import deform_conv2d
        return deform_conv2d
    except ImportError:
        return None


_DEFORM_CONV2D = _try_import_deform_conv2d()


class DCNv2_Conv(nn.Module):
    """
    Deformable ConvNets v2 (modulated DCN).

    Args:
        in_channels, out_channels: 通道数
        kernel_size, stride, padding, dilation: 同标准 Conv2d
        deformable_groups: 可变形组数 (默认 1)
        modulated:        是否启用调制标量 (DCNv2 特性, 默认 True)
    """
    def __init__(self, in_channels, out_channels, kernel_size=3,
                 stride=1, padding=None, dilation=1,
                 deformable_groups=1, modulated=True):
        super().__init__()
        if padding is None:
            padding = (kernel_size - 1) // 2 * dilation

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.dilation = dilation
        self.deformable_groups = deformable_groups
        self.modulated = modulated
        self._dcn_fn = _DEFORM_CONV2D  # 可能是 None

        # offset (2 * k * k * groups)
        self.conv_offset = nn.Conv2d(
            in_channels, deformable_groups * 2 * kernel_size * kernel_size,
            kernel_size=kernel_size, stride=stride,
            padding=padding, dilation=dilation, bias=True,
        )
        nn.init.constant_(self.conv_offset.weight, 0)
        nn.init.constant_(self.conv_offset.bias, 0)

        # mask (modulation, k * k * groups)
        if modulated:
            self.conv_mask = nn.Conv2d(
                in_channels, deformable_groups * kernel_size * kernel_size,
                kernel_size=kernel_size, stride=stride,
                padding=padding, dilation=dilation, bias=True,
            )
            nn.init.constant_(self.conv_mask.weight, 0)
            nn.init.constant_(self.conv_mask.bias, 0)
        else:
            self.conv_mask = None

        # 实际权重
        self.weight = nn.Parameter(torch.empty(
            out_channels, in_channels, kernel_size, kernel_size))
        nn.init.kaiming_normal_(self.weight, mode='fan_out', nonlinearity='relu')
        self.bias = nn.Parameter(torch.zeros(out_channels))

        self.bn = nn.BatchNorm2d(out_channels)
        self.act = nn.ReLU6(inplace=True)

    def forward(self, x):
        offset = self.conv_offset(x)
        mask = torch.sigmoid(self.conv_mask(x)) if self.conv_mask is not None else None

        if self._dcn_fn is not None:
            # B3 修复: 不再传 deformable_groups (函数从 offset 形状自动推断)
            out = self._dcn_fn(
                x, offset, self.weight, bias=self.bias,
                stride=(self.stride, self.stride),
                padding=(self.padding, self.padding),
                dilation=(self.dilation, self.dilation),
                mask=mask,
            )
        else:
            # torchvision 不支持时退化为标准卷积
            out = F.conv2d(x, self.weight, bias=self.bias,
                           stride=self.stride, padding=self.padding,
                           dilation=self.dilation)
        return self.act(self.bn(out))


class DeformableIRB(nn.Module):
    """Deformable Inverted Residual Block (用于替换 stage3/4)"""
    def __init__(self, in_ch, out_ch, stride=1, expand_ratio=4,
                 use_dcn=True, dcn_groups=1):
        super().__init__()
        assert stride in [1, 2]
        hidden_ch = int(round(in_ch * expand_ratio))
        self.use_res = (stride == 1 and in_ch == out_ch)

        self.expand = nn.Sequential(
            nn.Conv2d(in_ch, hidden_ch, 1, bias=False),
            nn.BatchNorm2d(hidden_ch),
            nn.ReLU6(inplace=True),
        )
        if use_dcn and _DEFORM_CONV2D is not None:
            self.dcn = DCNv2_Conv(
                hidden_ch, hidden_ch, kernel_size=3, stride=stride,
                deformable_groups=dcn_groups, modulated=True,
            )
        else:
            self.dcn = nn.Sequential(
                nn.Conv2d(hidden_ch, hidden_ch, 3, stride=stride,
                          padding=1, groups=hidden_ch, bias=False),
                nn.BatchNorm2d(hidden_ch),
                nn.ReLU6(inplace=True),
            )
        self.project = nn.Sequential(
            nn.Conv2d(hidden_ch, out_ch, 1, bias=False),
            nn.BatchNorm2d(out_ch),
        )

    def forward(self, x):
        out = self.project(self.dcn(self.expand(x)))
        if self.use_res:
            return x + out
        return out


class DCNStage(nn.Module):
    """DCN Stage: 1 个 DCN-IRB (含 stride) + N-1 个标准 IRB"""
    def __init__(self, in_ch, out_ch, num_blocks, first_stride=2,
                 use_dcn_first=True, expand_ratio=4, dcn_groups=1):
        super().__init__()
        self.blocks = nn.ModuleList([
            DeformableIRB(in_ch, out_ch, stride=first_stride,
                          expand_ratio=expand_ratio,
                          use_dcn=use_dcn_first, dcn_groups=dcn_groups)
        ])
        for _ in range(max(0, num_blocks - 1)):
            self.blocks.append(
                DeformableIRB(out_ch, out_ch, stride=1,
                              expand_ratio=expand_ratio, use_dcn=False)
            )

    def forward(self, x):
        for b in self.blocks:
            x = b(x)
        return x


def check_dcn_support():
    return _DEFORM_CONV2D is not None
