"""
tools/smoke_test.py
==============================================
端到端冒烟测试: 一次性体检 v5 → v6 的所有 bug 修复 + 新优化项.

设计原则:
  * 不依赖 GPU, CPU 上跑得通就算过
  * 不依赖数据集, 用合成 dummy 数据
  * 5 分钟内跑完
  * 对路径鲁棒 (Windows / Linux / Mac 均可, 任意 CWD)
  * 任意一项 FAIL 都打印具体原因, 不是只看 ✗

用法:
    python tools/smoke_test.py
    或
    cd tools && python smoke_test.py
"""
import os
import sys
import re
import ast
import importlib

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


PASS, FAIL = '✓', '✗'
results = []

# 路径解析: 不论 CWD 在哪, REPO_ROOT 永远是 smoke_test.py 的父目录的父目录
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, REPO_ROOT)


def _read(rel_path):
    """读取仓库内一个相对路径文件, 跨平台用正斜杠."""
    p = os.path.join(REPO_ROOT, *rel_path.split('/'))
    if not os.path.exists(p):
        raise FileNotFoundError(f'仓库缺文件: {rel_path}  (查找了 {p})')
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()


def check(name, fn):
    try:
        fn()
        print(f'  {PASS} {name}')
        results.append((name, True, None))
    except AssertionError as e:
        print(f'  {FAIL} {name}: {e}')
        results.append((name, False, str(e)))
    except Exception as e:
        print(f'  {FAIL} {name}: {type(e).__name__}: {e}')
        results.append((name, False, f'{type(e).__name__}: {e}'))


# ============================================================
# Round 1: v5 的 7 个 bug
# ============================================================
def test_b1_config_import():
    cfg = importlib.import_module('configs.config_setting')
    assert hasattr(cfg, 'setting_config'), 'setting_config 未导出'
    sc = cfg.setting_config
    assert hasattr(sc, 'criterion'), '配置缺 criterion'
    assert hasattr(sc, 'epochs') and sc.epochs > 0, '配置缺 epochs'


def test_b2_mixup_unpacking():
    from models.mixup_cutmix import seg_mixup_data, seg_cutmix_data
    x = torch.randn(2, 3, 64, 64)
    y = torch.randint(0, 7, (2, 64, 64))
    out = seg_mixup_data(x, y, alpha=0.5, num_classes=7)
    assert isinstance(out, tuple) and len(out) == 2, \
        f'seg_mixup_data 返回 {len(out)}-tuple, 与 train.py 期望的 2-tuple 不匹配'
    out2 = seg_cutmix_data(x, y, alpha=1.0, num_classes=7)
    assert len(out2) == 2


def test_b3_dcn_signature():
    src = _read('models/dcn.py')
    tree = ast.parse(src)
    bad = []
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == 'DCNv2_Conv':
            for fn in ast.walk(cls):
                if isinstance(fn, ast.FunctionDef) and fn.name == 'forward':
                    for call in ast.walk(fn):
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                            if call.func.attr == '_dcn_fn':
                                for kw in call.keywords:
                                    if kw.arg == 'deformable_groups':
                                        bad.append(kw.arg)
    assert not bad, f'DCN forward 给 _dcn_fn 传了 deformable_groups (会 TypeError): {bad}'

    parts = src.split('"""', 2)
    code_part = parts[-1] if len(parts) >= 3 else src
    assert 'torch.ops.ops.torch.ops' not in code_part, '代码段仍含 v5 占位符乱码'


def test_s1_swa_running_avg():
    from models.swa import SWA
    m = nn.Sequential(nn.Conv2d(1, 4, 3, padding=1))
    swa = SWA(m, swa_start=0, swa_lr=1e-4, device='cpu')

    snapshots = []
    with torch.no_grad():
        for _ in range(5):
            for p in m.parameters():
                p.copy_(torch.randn_like(p))
            snapshots.append([p.clone() for p in m.parameters()])
            swa.update()

    for i, p_avg in enumerate(swa.module_list):
        true_avg = torch.stack([s[i] for s in snapshots]).mean(0)
        diff = (p_avg - true_avg).abs().max().item()
        assert diff < 1e-5, f'SWA[{i}] 与真实平均误差 {diff:.3e}'


def test_s2_swa_lr_application():
    from models.swa import SWA
    m = nn.Sequential(nn.Conv2d(1, 4, 3, padding=1))
    opt = torch.optim.AdamW(m.parameters(), lr=5e-4)
    swa = SWA(m, swa_start=0, swa_lr=1e-4, device='cpu')
    assert opt.param_groups[0]['lr'] == 5e-4
    swa.apply_swa_lr_to_optimizer(opt)
    assert opt.param_groups[0]['lr'] == 1e-4, \
        f'apply 后 lr={opt.param_groups[0]["lr"]} 仍非 swa_lr'


def test_s3_best_swa_save_format():
    """用括号匹配从 train.py 抠出 torch.save(...) 调用, 确保第一个参数包含 state_dict"""
    src = _read('train.py')
    idx = 0
    found_args = None
    while True:
        idx = src.find('torch.save(', idx)
        if idx < 0:
            break
        depth = 1
        i = idx + len('torch.save(')
        start_args = i
        while i < len(src) and depth > 0:
            if src[i] == '(':
                depth += 1
            elif src[i] == ')':
                depth -= 1
            i += 1
        snippet = src[start_args:i - 1]
        if 'best_swa.pth' in snippet:
            found_args = snippet
            break
        idx = i

    assert found_args is not None, '没找到保存 best_swa.pth 的 torch.save() 调用'
    saved_arg = found_args.split(',', 1)[0]
    assert 'state_dict' in saved_arg, \
        f'best_swa.pth 保存的不是 state_dict, 实际是: {saved_arg.strip()[:120]}'


def test_s4_ema_bn_buffer():
    from models.ema import ModelEMA
    m = nn.Sequential(nn.Conv2d(3, 8, 3, padding=1), nn.BatchNorm2d(8), nn.ReLU())
    ema = ModelEMA(m, decay=0.5, warmup_steps=0, device='cpu')

    m.train()
    for _ in range(10):
        x = torch.randn(4, 3, 8, 8) * 5 + 3
        m(x)

    bn_main = m[1]
    bn_ema = ema.module[1]
    assert bn_main.running_mean.abs().sum() > 0.1, '主 BN 没动'
    initial_diff = bn_ema.running_mean.abs().sum().item()
    assert initial_diff < 0.01, 'EMA BN 初始值异常'

    for _ in range(5):
        ema.update()
    assert bn_ema.running_mean.abs().sum() > 0.05, \
        'EMA BN running_mean 仍接近 0, S4 未修复'


def test_s5_progressive_resizing_loader():
    from util.loader import TunnelDefectDataset

    class _Mock(TunnelDefectDataset):
        def __init__(self):
            self.img_size = (256, 256)
            self.samples = []
            self.crack_dilate = 0
            self.crack_copy_paste_prob = 0
            self.crack_elastic_prob = 0
            self.mosaic_prob = 0
            self.augment = False
            self.split = 'train'
            self.crack_pool = []

    ds = _Mock()
    assert hasattr(ds, 'update_input_size'), '缺 update_input_size 方法'
    ds.update_input_size(384, 384)
    assert ds.img_size == (384, 384)
    ds.update_input_size(512, 512)
    assert ds.img_size == (512, 512)


def test_s6_label_smoothing_not_destructive():
    from configs.config_setting import LabelSmoothingAddon, _topology_loss

    torch.manual_seed(0)
    logits = torch.randn(2, 7, 32, 32, requires_grad=True)
    ds_list = [torch.randn(2, 7, 32, 32, requires_grad=True) for _ in range(3)]
    targets = torch.zeros(2, 32, 32, dtype=torch.long)
    targets[:, 10:14, 5:25] = 1

    addon0 = LabelSmoothingAddon(_topology_loss, num_classes=7, smoothing=0.0)
    base = _topology_loss((logits, ds_list), targets)
    via_addon = addon0((logits, ds_list), targets)
    assert torch.allclose(base, via_addon, atol=1e-5), \
        f'smoothing=0 时不等同 base_loss: {base.item()} vs {via_addon.item()}'

    addon1 = LabelSmoothingAddon(_topology_loss, num_classes=7,
                                  smoothing=0.1, smoothing_weight=0.5)
    via_addon1 = addon1((logits, ds_list), targets)
    assert via_addon1.item() > base.item(), 'smoothing>0 时 addon 没增大 base loss'


def test_s7_forward_returns_both():
    from models.TunnelDefectSeg import TunnelDefectSeg
    m = TunnelDefectSeg(num_classes=7, backbone='custom_irb',
                         c_list=(16, 32, 64, 96, 128), aspp_ch=64,
                         aux_classifier=True, intermediate_head=True)
    m.train()
    x = torch.randn(2, 3, 128, 128)
    out = m(x, return_aux=True)
    assert isinstance(out, tuple) and len(out) == 3
    logits, ds_list, extras = out
    assert isinstance(extras, dict) and 'aux' in extras and 'im' in extras
    assert extras['aux'] is not None and extras['im'] is not None, \
        'aux 或 im 被丢 (S7 未修复)'


# ============================================================
# Round 2: 新优化项
# ============================================================
def test_extra_aspp_simplified():
    from models.TunnelDefectSeg import LiteASPP
    aspp = LiteASPP(in_ch=128, out_ch=64, rates=(6, 12))
    x = torch.randn(2, 128, 24, 24)
    y = aspp(x)
    assert y.shape == (2, 64, 24, 24)

    aspp2 = LiteASPP(in_ch=96, out_ch=64, rates=(2, 4))
    x2 = torch.randn(2, 96, 12, 12)
    y2 = aspp2(x2)
    assert y2.shape == (2, 64, 12, 12)


def test_extra_sliding_window_shape():
    from inference import sliding_window_inference
    from models.TunnelDefectSeg import TunnelDefectSeg

    m = TunnelDefectSeg(num_classes=7, backbone='custom_irb',
                         c_list=(16, 32, 64, 96, 128), aspp_ch=64).eval()
    img = (np.random.rand(500, 700, 3) * 255).astype(np.uint8)
    pred = sliding_window_inference(m, img, device='cpu',
                                     tile=128, overlap=32, use_tta=False)
    assert pred.shape == (500, 700)
    assert pred.dtype == np.int64
    assert pred.min() >= 0 and pred.max() < 7


# ============================================================
# Round 3: 评估指标
# ============================================================
def test_r3_tolerance_iou_rescues_shifted_pred():
    from util.metrics import compute_per_image
    gt = np.zeros((100, 100), dtype=np.int64)
    gt[40:43, 10:90] = 1
    pred = np.zeros_like(gt)
    pred[41:44, 10:90] = 1

    m = compute_per_image(pred, gt, num_classes=7, crack_class=1)
    assert m['IoU_class1'] < 0.7, f'plain IoU={m["IoU_class1"]}'
    assert m['crack_tol_iou'] > 0.95, f'Tol IoU={m["crack_tol_iou"]}'


def test_r3_perfect_prediction_all_one():
    from util.metrics import compute_per_image
    gt = np.zeros((50, 50), dtype=np.int64)
    gt[20:25, 5:45] = 1
    m = compute_per_image(gt.copy(), gt, num_classes=7, crack_class=1)
    for k in ('IoU_class1', 'crack_tol_iou', 'crack_bf1', 'crack_f1'):
        assert abs(m[k] - 1.0) < 1e-6, f'{k}={m[k]} 不是 1.0'


def test_r3_streaming_iou_equals_concat():
    from util.metrics import StreamingIoU, compute_metrics
    np.random.seed(42)
    imgs = [np.random.randint(0, 7, (20, 20)) for _ in range(15)]
    gts = [np.random.randint(0, 7, (20, 20)) for _ in range(15)]

    ref = compute_metrics(
        np.concatenate([p.ravel() for p in imgs]),
        np.concatenate([g.ravel() for g in gts]), num_classes=7)
    s = StreamingIoU(7)
    for p, g in zip(imgs, gts):
        s.update(p, g)
    got = s.compute()
    for k in ('mIoU', 'mDice', 'pixel_acc'):
        assert abs(ref[k] - got[k]) < 1e-9


def test_r3_test_py_imports_clean():
    src = _read('test.py')
    ast.parse(src)


def test_r3_multi_scale_present_in_inference():
    src = _read('inference.py')
    assert '--multi_scale' in src, 'inference.py 缺 --multi_scale CLI'
    assert 'scales=' in src, 'inference.py 没把 scales 传进来'


def test_r3_engine_has_rich_val():
    src = _read('util/engine.py')
    assert 'def val_one_epoch_rich' in src, '缺 val_one_epoch_rich 函数'
    assert 'def test_one_epoch(test_loader' in src, '老 test_one_epoch 被破坏'
    assert 'def val_one_epoch(val_loader' in src, '老 val_one_epoch 被破坏'


# ============================================================
# Driver
# ============================================================
def main():
    print('=' * 64)
    print('TunnelDefectSeg smoke test')
    print(f'REPO_ROOT = {REPO_ROOT}')
    print('=' * 64)

    print('\n[Round 1: v5 → v6 修复 7 个 bug]')
    check('B1 config_setting 可 import',          test_b1_config_import)
    check('B2 mixup 不再 4-tuple 错配',           test_b2_mixup_unpacking)
    check('B3 DCN forward 调用签名正确',           test_b3_dcn_signature)
    check('S1 SWA running average 数学正确',       test_s1_swa_running_avg)
    check('S2 swa_lr 真的应用到 optimizer',        test_s2_swa_lr_application)
    check('S3 best_swa.pth 保存完整 state_dict',  test_s3_best_swa_save_format)
    check('S4 EMA BN buffer 真的更新',             test_s4_ema_bn_buffer)
    check('S5 PR 切分辨率 dataset 真的响应',       test_s5_progressive_resizing_loader)
    check('S6 LabelSmoothing 不再吃掉 base loss', test_s6_label_smoothing_not_destructive)
    check('S7 forward 同时返回 aux 与 im',         test_s7_forward_returns_both)

    print('\n[Round 2: 新优化项]')
    check('简化 ASPP (4 分支) 形状正确',           test_extra_aspp_simplified)
    check('滑窗推理输出尺寸正确',                   test_extra_sliding_window_shape)

    print('\n[Round 3: 评估指标]')
    check('Tolerance IoU 拯救 1 px 偏移',           test_r3_tolerance_iou_rescues_shifted_pred)
    check('完美预测时所有新指标 = 1.0',             test_r3_perfect_prediction_all_one)
    check('StreamingIoU == concat-based',           test_r3_streaming_iou_equals_concat)
    check('test.py 可 AST parse',                   test_r3_test_py_imports_clean)
    check('inference.py 支持 --multi_scale',        test_r3_multi_scale_present_in_inference)
    check('engine.py 增加 val_one_epoch_rich',      test_r3_engine_has_rich_val)

    print('\n' + '=' * 64)
    failed = [(n, e) for n, ok, e in results if not ok]
    if failed:
        print(f'FAILED ({len(failed)}/{len(results)}):')
        for n, e in failed:
            print(f'  - {n}')
            print(f'      {e}')
        sys.exit(1)
    else:
        print(f'ALL {len(results)} CHECKS PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()
