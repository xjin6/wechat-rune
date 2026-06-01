"""Build {md5(decrypted) -> {thumb, hd, original, animated}} index for fast lookup.

`animated` distinguishes real animated stickers (wxgf-container HEVC, first 4
bytes = 'wxgf') from static iPhone HEIC screenshots (also .hevc extension but
no wxgf magic). PNG/JPG/etc are always animated=False.
"""
import hashlib
import json
import os
import sys
import io
from pathlib import Path
from collections import defaultdict

def is_animated_file(path: Path) -> bool:
    """True only for wxgf-magic HEVC animations. iPhone HEIC files (also .hevc)
    don't start with 'wxgf' and so return False."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        return head == b"wxgf"
    except Exception:
        return False

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

if len(sys.argv) < 3:
    print("Usage: build_image_index.py <decrypted_dir> <output_json>")
    sys.exit(2)

dec_dir = Path(sys.argv[1])
out_path = sys.argv[2]

groups = defaultdict(dict)  # (folder, base_id) -> {kind: relpath}
for path in dec_dir.rglob("*"):
    if not path.is_file():
        continue
    stem = path.stem
    if stem.endswith("_t"):
        kind, base_id = "t", stem[:-2]
    elif stem.endswith("_h"):
        kind, base_id = "h", stem[:-2]
    else:
        kind, base_id = "o", stem
    rel = path.relative_to(dec_dir).as_posix()
    folder = str(path.parent.relative_to(dec_dir).as_posix())
    groups[(folder, base_id)][kind] = rel

# For each group, md5 each file → build index keyed by md5
# Index value: dict with 'thumb', 'hd', 'orig' rel paths + the md5 itself
index = {}
md5_collision = 0
ok = 0
for (folder, base_id), members in groups.items():
    entry = {"thumb": members.get("t"), "hd": members.get("h"), "orig": members.get("o")}
    # The MD5 of decrypted bytes — message XML uses MD5 of the ORIGINAL image
    # (full / HD / no-suffix). Compute md5 of HD if present, else orig, else thumb.
    primary = members.get("h") or members.get("o") or members.get("t")
    if not primary:
        continue
    full_path = dec_dir / primary
    try:
        data = full_path.read_bytes()
        md5 = hashlib.md5(data).hexdigest()
    except Exception as e:
        continue
    if md5 in index:
        md5_collision += 1
    # Animated only if the primary is wxgf-magic HEVC (real WeChat sticker)
    entry["animated"] = is_animated_file(full_path) if primary.endswith(".hevc") else False
    index[md5] = entry
    ok += 1

# Also index thumbnails separately so we can find by either md5
thumb_index = {}
for (folder, base_id), members in groups.items():
    if "t" not in members:
        continue
    rel = members["t"]
    try:
        data = (dec_dir / rel).read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        if md5 not in thumb_index:
            thumb_index[md5] = rel
    except Exception:
        continue

# Write both
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"by_orig_md5": index, "by_thumb_md5": thumb_index}, f, ensure_ascii=False, indent=2)

print(f"Indexed {ok} image groups (orig-md5 keys: {len(index)}, collisions: {md5_collision})")
print(f"Thumb-md5 keys: {len(thumb_index)}")
print(f"Saved -> {out_path}")
