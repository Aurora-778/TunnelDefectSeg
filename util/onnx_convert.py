"""
ONNX 导出脚本
===============
用法:
    python util/onnx_convert.py --weights best.pth --output model.onnx
"""

import os
import sys
import argparse
import torch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.TunnelDefectSeg import TunnelDefectSeg
from configs.class_config import NUM_CLASSES


def parse_args():
    parser = argparse.ArgumentParser(description='ONNX 导出')
    parser.add_argument('--weights', type=str, required=True)
    parser.add_argument('--output', type=str, default='model.onnx')
    parser.add_argument('--img_size', type=int, default=384)
    parser.add_argument('--c_list', type=str, default='16,32,64,96,128',
                        help='训练时使用的通道配置')
    parser.add_argument('--opset', type=int, default=13)
    parser.add_argument('--dynamic', action='store_true',
                        help='动态 batch 轴')
    return parser.parse_args()


def main():
    args = parse_args()
    c_list = tuple(int(x) for x in args.c_list.split(','))

    # 构建模型 (推理模式, 无深度监督分支参与计算)
    model = TunnelDefectSeg(num_classes=NUM_CLASSES, c_list=c_list)
    state_dict = torch.load(args.weights, map_location='cpu')
    model.load_state_dict(state_dict)
    model.eval()

    dummy_input = torch.randn(1, 3, args.img_size, args.img_size)

    dynamic_axes = None
    if args.dynamic:
        dynamic_axes = {'input': {0: 'batch'}, 'output': {0: 'batch'}}

    torch.onnx.export(
        model,
        dummy_input,
        args.output,
        export_params=True,
        opset_version=args.opset,
        do_constant_folding=True,
        input_names=['input'],
        output_names=['output'],
        dynamic_axes=dynamic_axes,
    )
    print(f'已导出: {args.output}')
    print(f'  输入形状: [1, 3, {args.img_size}, {args.img_size}]')
    print(f'  输出形状: [1, {NUM_CLASSES}, {args.img_size}, {args.img_size}]')
    print(f'  动态 batch: {bool(dynamic_axes)}')

    # 可选: 用 onnxruntime 简单验证
    try:
        import onnxruntime as ort
        session = ort.InferenceSession(args.output, providers=['CPUExecutionProvider'])
        output = session.run(None, {'input': dummy_input.numpy()})
        print(f'  ONNX 推理验证 OK, 输出 shape: {output[0].shape}')
    except ImportError:
        pass


if __name__ == '__main__':
    main()
