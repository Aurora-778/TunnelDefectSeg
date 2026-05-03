"""
swa.py  (修复版)
=============================================
修复内容:
  [S1] update() 公式错: 原版 p_avg += p_curr/n , 老参数从未衰减
       正确: p_avg = (1-α)·p_avg + α·p_curr  (α = 1/n_averaged)
  [S2] 提供 apply_swa_lr_to_optimizer() 让外部把 swa_lr 真正写到 optimizer
  - sync_from_model 不再把 n_averaged 初始化为 1; 改为 0,
    第一次 update 才计入第 1 个样本, 语义和 torch.optim.swa_utils 一致
  - state_dict 用 list[Tensor] 序列化, 避免不必要的 deepcopy 体积膨胀

参考:
  Izmailov et al. UAI 2018  +  PyTorch torch.optim.swa_utils 官方实现
"""

import torch
import torch.nn as nn


class SWA:
    """
    Stochastic Weight Averaging.

    用法:
        swa = SWA(model, swa_start=150, swa_lr=1e-4)

        for epoch in range(1, total_epochs+1):
            train_one_epoch(...)

            if epoch >= swa.swa_start:
                # 1) 切到 swa_lr (只在第一次进入 SWA 阶段时)
                if epoch == swa.swa_start:
                    swa.apply_swa_lr_to_optimizer(optimizer)
                # 2) 累一次平均
                swa.update()

        # 训练结束: 把平均权重写回 model
        swa.write_back_to_model()
        update_bn(model, train_loader)   # 重新跑一遍 BN running stats
    """

    def __init__(self, model, swa_start=None, swa_lr=1e-4, device='cuda'):
        self.model = model
        self.swa_start = swa_start if swa_start is not None else float('inf')
        self.swa_lr = swa_lr
        self.device = device

        # 影子参数列表 (与 model.parameters() 一一对应)
        self.module_list = [p.detach().clone() for p in model.parameters()]
        self.n_averaged = 0  # 0 表示尚未累入任何快照

    # ----------------------------------------------------------
    # 累一次快照 (S1: 修正公式)
    # ----------------------------------------------------------
    @torch.no_grad()
    def update(self):
        """正确的 cumulative average:  p_avg ← (1-α)·p_avg + α·p_curr"""
        self.n_averaged += 1
        alpha = 1.0 / self.n_averaged
        for p_avg, p_curr in zip(self.module_list, self.model.parameters()):
            # in-place: p_avg ← p_avg + (p_curr - p_avg) * α
            p_avg.add_(p_curr.detach() - p_avg, alpha=alpha)

    # ----------------------------------------------------------
    # 写回 / 备份
    # ----------------------------------------------------------
    @torch.no_grad()
    def write_back_to_model(self):
        """把累出来的平均权重写到原 model 里"""
        if self.n_averaged == 0:
            return False
        for p_avg, p_curr in zip(self.module_list, self.model.parameters()):
            p_curr.data.copy_(p_avg)
        return True

    # 兼容老接口名
    load_swa_weights = write_back_to_model

    @torch.no_grad()
    def sync_from_model(self):
        """重置: 把当前 model 的参数 copy 进 module_list, 计数清零"""
        self.module_list = [p.detach().clone() for p in self.model.parameters()]
        self.n_averaged = 0

    # ----------------------------------------------------------
    # S2: 真正应用 swa_lr 到 optimizer
    # ----------------------------------------------------------
    def apply_swa_lr_to_optimizer(self, optimizer):
        """
        进入 SWA 阶段时调用一次, 把所有 param group 的 lr 锁定到 swa_lr。
        之后调用方应停止 scheduler.step() 或自行决定是否做 swa_lr 周期变化。
        """
        for pg in optimizer.param_groups:
            pg['lr'] = self.swa_lr

    # ----------------------------------------------------------
    # 序列化 (S3 配套)
    # ----------------------------------------------------------
    def state_dict(self):
        # 不再深拷贝模型, 只保存 list[Tensor] - 后续 best_swa.pth
        # 直接保存当前 model.state_dict() 即可 (见 train.py)
        return {
            'module_list': [p.detach().cpu() for p in self.module_list],
            'n_averaged': self.n_averaged,
            'swa_start': self.swa_start,
            'swa_lr': self.swa_lr,
        }

    def load_state_dict(self, sd):
        # 设备对齐
        self.module_list = [p.to(self.device) for p in sd['module_list']]
        self.n_averaged = sd['n_averaged']
        self.swa_start = sd.get('swa_start', self.swa_start)
        self.swa_lr = sd.get('swa_lr', self.swa_lr)


# ============================================================
# update_swa: 兼容旧 import
# ============================================================
def update_swa(swa):
    """训练结束时调用, 把累计平均写回模型"""
    if swa.n_averaged == 0:
        return False
    swa.write_back_to_model()
    print(f'[SWA] applied averaged weights (n_averaged={swa.n_averaged})')
    return True


# ============================================================
# update_bn: SWA 后重置 BN running stats
# ============================================================
@torch.no_grad()
def update_bn(model, dataloader, device='cuda'):
    """
    用整个 dataloader 跑一遍前向, 让 BN 的 running_mean / running_var
    用 SWA 平均后的权重重新统计。否则 BN buffer 仍是训练末期的快照值,
    不匹配新的卷积权重, 精度会掉。
    """
    bn_layers = [
        m for m in model.modules()
        if isinstance(m, (nn.BatchNorm1d, nn.BatchNorm2d, nn.SyncBatchNorm))
    ]
    if not bn_layers:
        return

    # 备份 momentum, 训练时改成 None 让其用 cumulative moving average
    momenta = {}
    for m in bn_layers:
        momenta[m] = m.momentum
        m.momentum = None
        m.reset_running_stats()

    was_training = model.training
    model.train()
    for batch in dataloader:
        # 兼容 (img, target) / (img,)
        x = batch[0] if isinstance(batch, (tuple, list)) else batch
        x = x.to(device, non_blocking=True).float()
        model(x)

    for m, mom in momenta.items():
        m.momentum = mom
    model.train(was_training)
