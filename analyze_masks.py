import numpy as np
from PIL import Image
import os, glob, sys

root = r'C:\Users\26822\Downloads\data'
mask_files = []
for sub in ['1','2','3','4','5']:
    masks = glob.glob(os.path.join(root, sub, 'labels', '*_mask.png'))
    mask_files.extend(masks[:5])

print(f'Total: {len(mask_files)}')
print()
for mf in mask_files[:20]:
    mask = np.array(Image.open(mf).convert('L'))
    unique = np.unique(mask)
    nonzero = mask[mask > 0]
    nonzero_max = nonzero.max() if len(nonzero) > 0 else 0
    nonzero_count = len(nonzero)
    print(f'{os.path.basename(mf):35s} unique={unique!s:20s}  nonzero={nonzero_count:8d}  max={nonzero_max}')
