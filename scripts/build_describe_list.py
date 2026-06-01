"""
Walk the message DB, identify all [图片] (non-animated) image messages
that map to a local file, and dump a flat list ready for batched description.

Output: JSON list of [{content_md5, thumb, hd, orig, ts, sender}, ...]
"""
import sqlcipher3 as _sc
import hashlib
import json
import os
import re
import sys
import zstandard
from pathlib import Path

if len(sys.argv) < 4:
    print("Usage: build_describe_list.py <wxid> <image_index.json> <out.json>")
    sys.exit(2)

wxid = sys.argv[1]
index_path = sys.argv[2]
out_path = sys.argv[3]

keys = json.load(open(r"scripts/keys/wechat_keys.json"))
db_dir = r"D:/WeChat history/xwechat_files/magicxinjx_c092/db_storage"
tbl = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()

index = json.load(open(index_path, encoding="utf-8"))
by_orig = index["by_orig_md5"]

MD5_RE = re.compile(r'\bmd5="([a-f0-9]{32})"')
ORIGSRC_RE = re.compile(r'\boriginsourcemd5="([a-f0-9]*)"')

out = []
seen_md5 = set()

for shard in ["message_0.db", "message_1.db"]:
    db = f"{db_dir}/message/{shard}"
    rel = f"magicxinjx_c092\\db_storage\\message\\{shard}"
    key = keys.get(rel)
    if not key:
        continue
    c = _sc.connect(db)
    c.execute(f'PRAGMA key = "x\'{key}\'"')
    c.execute("PRAGMA cipher_page_size = 4096")
    rows = c.execute(
        f"SELECT create_time, real_sender_id, message_content "
        f"FROM {tbl} WHERE local_type=3 ORDER BY create_time ASC"
    ).fetchall()
    for ts, sender_id, content in rows:
        if not content:
            continue
        if content[:4] == b"\x28\xb5\x2f\xfd":
            try:
                xml = zstandard.decompress(content).decode("utf-8", errors="replace")
            except Exception:
                continue
        else:
            xml = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        m = MD5_RE.search(xml)
        if not m:
            continue
        md5 = m.group(1)
        if md5 in seen_md5:
            continue
        if md5 not in by_orig:
            continue
        entry = by_orig[md5]
        # Skip animated (originsourcemd5 empty)
        osrc = ORIGSRC_RE.search(xml)
        is_animated = bool(osrc and not osrc.group(1))
        if is_animated:
            continue
        seen_md5.add(md5)
        out.append({
            "ts": ts,
            "sender_id": sender_id,
            "content_md5": md5,
            "thumb": entry.get("thumb"),
            "hd": entry.get("hd"),
            "orig": entry.get("orig"),
        })
    c.close()

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"Wrote {len(out)} entries -> {out_path}")
