"""
ema.py  (修复版)
=============================================
修复内容:
  [S4] BN running_mean / running_var 的 EMA 在原版完全不生效
       原因: 原版 isinstance(buffer_tensor, BatchNorm2d) 永远 False
       影响: EMA 模型的 BN buffer 停留在初始化值, 评估精度被严重低估
"""
import torch
from copy import deepcopy


class ModelEMA:
    """
    PyTorch 轻量 EMA 实现 (修复 BN buffer 同步).
    """
    def __init__(self, model, decay=0.9998, warmup_steps=2000,
                 device=None, update_interval=1):
        self.model = model
        self.decay = decay
        self.warmup_steps = warmup_steps
        self.update_interval = update_interval
        self._step_count = 0

        # 影子模型 (深拷贝, eval 模式)
        self.module = deepcopy(model)
        for p in self.module.parameters():
            p.detach_()
        self.module.eval()

        if device is not None:
            self.module = self.module.to(device)

    def _get_decay(self):
        if self.warmup_steps <= 0:
            return self.decay
        step = max(1, self._step_count)
        if step >= self.warmup_steps:
            return self.decay
        return self.decay * (step / self.warmup_steps)

    @torch.no_grad()
    def update(self):
        """每个 train step 后调用一次"""
        self._step_count += 1
        if self._step_count % self.update_interval != 0:
            return

        decay = self._get_decay()

        # ---- 参数 EMA ----
        for emp, src in zip(self.module.parameters(), self.model.parameters()):
            emp.data.mul_(decay).add_(src.data.to(emp.data.device), alpha=1 - decay)

        # ---- (S4 修复) Buffer EMA ----
        # buffers() 返回的是 Tensor, 不是 BatchNorm module。
        # 区分浮点 buffer (running_mean/var) 与整型 buffer (num_batches_tracked):
        #   * 浮点 → 做 EMA
        #   * 整型 → 直接 copy (它本身只是个计数器, 平均没有意义)
        for emp_buf, src_buf in zip(self.module.buffers(), self.model.buffers()):
            src_data = src_buf.data.to(emp_buf.data.device)
            if emp_buf.dtype.is_floating_point:
                emp_buf.data.mul_(decay).add_(src_data, alpha=1 - decay)
            else:
                emp_buf.data.copy_(src_data)

    def update_attr(self, include=(), exclude=('module',)):
        for name, obj in self.model.__dict__.items():
            if name not in exclude and (
                    not include or any(pat in name for pat in include)):
                setattr(self.module, name, obj)

    def state_dict(self):
        return {
            'module': self.module.state_dict(),
            'decay': self.decay,
            'warmup_steps': self.warmup_steps,
            'step': self._step_count,
        }

    def load_state_dict(self, ckpt):
        self.module.load_state_dict(ckpt['module'])
        self._step_count = ckpt.get('step', 0)
