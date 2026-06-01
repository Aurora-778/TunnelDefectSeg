r"""
ResNet50 FCN baseline v2

This entry point is kept for compatibility, but it now reuses the same
standardized baseline pipeline as train_resnet50.py so that all experiments
share the same:
  - num_classes = 6
  - input size  = 384x384
  - epochs      = 200
  - split       = 700/150/150
"""

from train_resnet50 import (
    Config,
    CrackDataset,
    ResNet50SegmentationModel as ResNet50FCN,
    discover_samples as discover,
    split_dataset as split,
    compute_metrics,
    compute_model_stats,
    measure_fps,
    train_one_epoch,
    validate,
    save_predictions,
    main as _main,
)

__all__ = [
    "Config",
    "CrackDataset",
    "ResNet50FCN",
    "discover",
    "split",
    "compute_metrics",
    "compute_model_stats",
    "measure_fps",
    "train_one_epoch",
    "validate",
    "save_predictions",
    "main",
]


def main():
    _main()


if __name__ == "__main__":
    main()
