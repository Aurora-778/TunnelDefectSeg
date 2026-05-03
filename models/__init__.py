# models/__init__.py - TunnelDefectSeg v5
# ===================================================
# 所有 v3/v4 模块继续可用, 新增 v5 模块.
# v5 新增: SWA, DCN v2, Progressive Resizing

from .TunnelDefectSeg import TunnelDefectSeg, build_model
from .TunnelDefectSeg import (
    ConvBNAct, DWSepConv, ECA, CBAM,
    InvertedResidual, StripPool,
    LiteASPP, FeatureFusion,
    AuxClassifier,                       # v4 新增
    IntermediateSegHead,                # v4 新增
)
from .ema import ModelEMA               # v4 新增
from .tta import (                     # v4 新增
    tta_inference, TTA_Balance, TTA_Fast, TTA_Accurate, TTA_Plus3,
)
from .mixup_cutmix import (            # v4 新增
    seg_mixup_data, seg_cutmix_data,
    MixupScheduler, GridMask,
    apply_mixup_or_cutmix,
)
# v5 新增
from .swa import SWA, update_swa, update_bn
from .dcn import (
    DCNv2_Conv, DeformableIRB, DCNStage,
    check_dcn_support,
)
from .progressive_resizing import (
    ProgressiveResizing, ProgressiveDataLoader,
    resize_to_target, resize_for_mask,
)

__all__ = [
    # v3 模型组件
    'TunnelDefectSeg', 'build_model',
    'ConvBNAct', 'DWSepConv', 'ECA', 'CBAM',
    'InvertedResidual', 'StripPool',
    'LiteASPP', 'FeatureFusion',
    # v4 新增模型组件
    'AuxClassifier', 'IntermediateSegHead',
    # v4 新增训练工具
    'ModelEMA',
    'tta_inference', 'TTA_Balance', 'TTA_Fast', 'TTA_Accurate', 'TTA_Plus3',
    'seg_mixup_data', 'seg_cutmix_data',
    'MixupScheduler', 'GridMask',
    'apply_mixup_or_cutmix',
    # v5 新增
    'SWA', 'update_swa', 'update_bn',
    'DCNv2_Conv', 'DeformableIRB', 'DCNStage', 'check_dcn_support',
    'ProgressiveResizing', 'ProgressiveDataLoader',
    'resize_to_target', 'resize_for_mask',
]