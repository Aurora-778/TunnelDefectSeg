import json
from pathlib import Path

ann_dir = Path("C:/Users/26822/Downloads/data/annotations")
for split in ["train", "val"]:
    p = ann_dir / (split + ".json")
    d = json.loads(p.read_text(encoding="utf-8"))
    imgs = len(d["images"])
    anns = len(d["annotations"])
    cats = d["categories"]
    imgs_with_ann = len(set(a["image_id"] for a in d["annotations"]))
    bg_imgs = imgs - imgs_with_ann
    print(f"{split}: {imgs} 图像, {anns} 标注实例, {imgs_with_ann} 有裂缝, {bg_imgs} 背景图")
    print(f"  类别: {cats}")
    if d["annotations"]:
        s = d["annotations"][0]
        print(f"  示例标注: image_id={s['image_id']}, bbox={[round(x,1) for x in s['bbox']]}, area={round(s['area'])}")

print("\nJSON 文件大小:")
for f in sorted(ann_dir.glob("*.json")):
    print(f"  {f.name}: {f.stat().st_size/1024:.1f} KB")
