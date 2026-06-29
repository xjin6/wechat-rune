"""Incremental-update diagnostic: compare current WeChat DB state against
existing exported artifacts (voice_map, image_descriptions, decrypted images).

Run before any incremental update to see per-contact deltas:
  - new DB messages / voices / image-md5s
  - voice_map: existing, NEW (need transcription), stale (in map not DB)
  - image_descriptions: existing, discoverable (DB & index), NEW to describe
  - attach .dat files on disk vs decrypted output (new files to decrypt)

Usage:
  python scripts/incremental_diff.py <relationship_root> <wxid:label> [<wxid:label> ...]
  # relationship_root expects this per-contact layout (the cache JSONs live in
  # the archive subfolder; images/ stays alongside the .md so markdown refs
  # resolve as-is). Legacy flat layout (all JSONs at <label>/ top level) is
  # still accepted as a fallback.
  #   <label>/
  #     <label>_wechat.md
  #     images/
  #     <label>_archive/
  #       <label>_voice_map.json
  #       image_index.json
  #       image_descriptions.json
  #       describe_list.json
  #       describe_batches/
  #       voice_batches/

Example:
  python scripts/incremental_diff.py "<relationship root>" \\
    "wxid_aaaaaaaaaaaa:alice" "wxid_bbbbbbbbbbbb:bob"

No mutations — pure reporting. The actual incremental updates use:
  - transcribe_voices.py --out <existing_path>  (native resume mode)
  - decrypt_images.py                            (idempotent over existing files)
  - build_image_index.py + build_describe_list.py
  - Then diff describe_list vs image_descriptions → describe net-new only
"""
import sys, os, json, hashlib, re
from sqlcipher3 import dbapi2 as sc
import zstandard


def find_keys_file() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        cand = os.path.join(here, "keys", "wechat_keys.json")
        if os.path.exists(cand):
            return cand
        here = os.path.dirname(here)
    sys.exit("Cannot find scripts/keys/wechat_keys.json. Run extract_key_windows.py first.")


def detect_db_and_attach() -> tuple[str, str]:
    """Return (db_dir, attach_root) by inspecting any key path."""
    keys = json.load(open(find_keys_file()))
    sample = next(iter(keys.keys()))  # e.g. "myaccount_a1b2\\db_storage\\message\\message_0.db"
    # Use XWECHAT_FILES detection from config.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import XWECHAT_FILES

    # Windows-style key path includes the account folder as the first segment
    # ("myaccount_a1b2\\db_storage\\message\\message_0.db"). Mac-style omits it
    # ("message/message_0.db"). Detect by the path separator.
    if "\\" in sample:
        account_folder = sample.split("\\")[0]
    else:
        # Mac: scan XWECHAT_FILES for the account folder (the one containing db_storage)
        try:
            account_folder = next(
                d for d in os.listdir(XWECHAT_FILES)
                if os.path.isdir(os.path.join(XWECHAT_FILES, d, "db_storage"))
            )
        except (StopIteration, FileNotFoundError):
            sys.exit(f"No xwechat account folder found under {XWECHAT_FILES}")

    base = os.path.join(XWECHAT_FILES, account_folder)
    return os.path.join(base, "db_storage"), os.path.join(base, "msg", "attach")


def shard_key(keys: dict, db_dir: str, shard: str) -> str:
    """Look up the per-shard key by matching the relative path pattern."""
    pat = f"message/{shard}"
    for k, v in keys.items():
        if pat in k.replace("\\", "/"):
            return v
    return ""


MD5_RE = re.compile(r'\bmd5="([a-f0-9]{32})"')


def scan_contact(keys: dict, db_dir: str, wxid: str) -> dict:
    """Return DB stats for a contact: msg count, voice ts set, image md5 set."""
    tbl = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()
    total_msgs = 0
    voice_ts: set[str] = set()
    image_md5s: set[str] = set()
    msg_dir = os.path.join(db_dir, "message")
    if not os.path.isdir(msg_dir):
        return {"total_msgs": 0, "voice_ts": voice_ts, "image_md5s": image_md5s, "shards_missing": []}
    shards = sorted(f for f in os.listdir(msg_dir) if re.match(r"message_\d+\.db$", f))
    missing_shards: list[str] = []
    for shard in shards:
        db = os.path.join(msg_dir, shard)
        k = shard_key(keys, db_dir, shard)
        if not k:
            continue
        c = sc.connect(db)
        c.execute(f'PRAGMA key="x\'{k}\'"')
        c.execute("PRAGMA cipher_page_size=4096")
        try:
            total_msgs += c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            for (ts,) in c.execute(f"SELECT create_time FROM {tbl} WHERE local_type=34"):
                voice_ts.add(str(ts))
            for (content,) in c.execute(f"SELECT message_content FROM {tbl} WHERE local_type=3"):
                if not content:
                    continue
                if content[:4] == b"\x28\xb5\x2f\xfd":
                    try:
                        xml = zstandard.decompress(content).decode("utf-8", "replace")
                    except Exception:
                        continue
                else:
                    xml = content.decode("utf-8", "replace")
                m = MD5_RE.search(xml)
                if m:
                    image_md5s.add(m.group(1))
        except sc.OperationalError:
            missing_shards.append(shard)
        c.close()
    return {"total_msgs": total_msgs, "voice_ts": voice_ts, "image_md5s": image_md5s, "shards_missing": missing_shards}


def report(label: str, wxid: str, rel_root: str, keys: dict, db_dir: str, attach_root: str):
    print(f"\n===== {label} ({wxid}) =====")
    stats = scan_contact(keys, db_dir, wxid)
    print(f"  DB messages: {stats['total_msgs']}")
    print(f"  DB voice messages: {len(stats['voice_ts'])}")
    print(f"  DB image XML md5s: {len(stats['image_md5s'])}")
    for s in stats["shards_missing"]:
        print(f"  ⚠ shard {s}: contact's Msg_ table not present (open this chat in Weixin and re-extract)")

    # Cache JSONs live under <label>/<label>_archive/. Fall back to the legacy
    # flat layout (<label>/) for backward compat with un-migrated contacts.
    archive_dir = os.path.join(rel_root, label, f"{label}_archive")
    legacy_dir  = os.path.join(rel_root, label)

    def _cache(filename: str) -> str:
        new = os.path.join(archive_dir, filename)
        return new if os.path.exists(new) else os.path.join(legacy_dir, filename)

    vmap_path = _cache(f"{label}_voice_map.json")
    if os.path.exists(vmap_path):
        vmap = json.load(open(vmap_path, encoding="utf-8"))
        new_v = stats["voice_ts"] - set(vmap.keys())
        stale = set(vmap.keys()) - stats["voice_ts"]
        print(f"  voice_map: {len(vmap)} existing | NEW: {len(new_v)} | stale: {len(stale)}")
    else:
        print(f"  voice_map: MISSING — full transcription of {len(stats['voice_ts'])} entries needed")

    idx_path = _cache("image_index.json")
    desc_path = _cache("image_descriptions.json")
    if os.path.exists(idx_path) and os.path.exists(desc_path):
        idx = json.load(open(idx_path, encoding="utf-8"))
        desc = json.load(open(desc_path, encoding="utf-8"))
        discoverable = stats["image_md5s"] & set(idx.get("by_orig_md5", {}).keys())
        new_d = discoverable - set(desc.keys())
        print(f"  image_descriptions: {len(desc)} existing | discoverable: {len(discoverable)} | NEW: {len(new_d)}")
    else:
        print(f"  image_descriptions or image_index: MISSING — full rebuild needed")

    contact_md5 = hashlib.md5(wxid.encode()).hexdigest()
    src_dir = os.path.join(attach_root, contact_md5)
    # images/ stays alongside the .md (not in archive) so md image refs work as-is
    out_dir = os.path.join(rel_root, label, "images")
    n_src = sum(len(fs) for _, _, fs in os.walk(src_dir)) if os.path.isdir(src_dir) else 0
    n_out = sum(len(fs) for _, _, fs in os.walk(out_dir)) if os.path.isdir(out_dir) else 0
    print(f"  attach .dat files: {n_src} | decrypted: {n_out} | new to decrypt: {max(0, n_src - n_out)}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    rel_root = sys.argv[1]
    pairs = []
    for arg in sys.argv[2:]:
        if ":" not in arg:
            sys.exit(f"Bad arg {arg!r}: expected wxid:label")
        wxid, label = arg.split(":", 1)
        pairs.append((wxid, label))
    keys = json.load(open(find_keys_file()))
    db_dir, attach_root = detect_db_and_attach()
    for wxid, label in pairs:
        report(label, wxid, rel_root, keys, db_dir, attach_root)


if __name__ == "__main__":
    main()
