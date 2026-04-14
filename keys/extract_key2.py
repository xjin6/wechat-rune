import re, json, os, struct, hashlib, hmac as hmac_mod, glob

DB_DIR = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16

def collect_db_files():
    pattern = os.path.join(DB_DIR, "*", "db_storage", "**", "*.db")
    db_files = []
    salt_to_dbs = {}
    for path in glob.glob(pattern, recursive=True):
        if path.endswith("-wal") or path.endswith("-shm"):
            continue
        sz = os.path.getsize(path)
        if sz < PAGE_SZ:
            continue
        with open(path, "rb") as fh:
            page1 = fh.read(PAGE_SZ)
        salt = page1[:SALT_SZ].hex()
        rel = os.path.relpath(path, DB_DIR)
        db_files.append((rel, path, sz, salt, page1))
        salt_to_dbs.setdefault(salt, []).append(rel)
    return db_files, salt_to_dbs

def verify_key(enc_key_bytes, db_page1):
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key_bytes, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ : PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64 : PAGE_SZ]
    h = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == stored_hmac

def run(process):
    db_files, salt_to_dbs = collect_db_files()
    print("Found %d databases" % len(db_files))

    HEX_PATTERN = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
    error = lldb.SBError()
    key_map = {}
    remaining_salts = set(salt_to_dbs.keys())

    region_info = lldb.SBMemoryRegionInfo()
    addr = 0
    regions = []
    while True:
        err = process.GetMemoryRegionInfo(addr, region_info)
        if err.Fail():
            break
        base = region_info.GetRegionBase()
        end = region_info.GetRegionEnd()
        if end <= base:
            break
        if region_info.IsReadable() and not region_info.IsExecutable():
            size = end - base
            if 0 < size < 500 * 1024 * 1024:
                regions.append((base, size))
        addr = end
        if addr == 0:
            break

    print("Scanning %d regions..." % len(regions))

    for base, size in regions:
        chunk_sz = 8 * 1024 * 1024
        offset = 0
        while offset < size:
            read_size = min(chunk_sz, size - offset)
            data = process.ReadMemory(base + offset, read_size, error)
            offset += read_size
            if not error.Success() or not data:
                continue
            for m in HEX_PATTERN.finditer(data):
                hex_str = m.group(1).decode()
                hex_len = len(hex_str)
                if hex_len == 96:
                    enc_key_hex = hex_str[:64]
                    salt_hex = hex_str[64:]
                elif hex_len == 64:
                    enc_key_hex = hex_str
                    salt_hex = None
                else:
                    continue
                if salt_hex and salt_hex in remaining_salts:
                    enc_key = bytes.fromhex(enc_key_hex)
                    for rel, path, sz, s, page1 in db_files:
                        if s == salt_hex and verify_key(enc_key, page1):
                            key_map[salt_hex] = enc_key_hex
                            remaining_salts.discard(salt_hex)
                            print("KEY: %s -> %s" % (rel, enc_key_hex))
                            break
        if not remaining_salts:
            break

    # Save
    result = {}
    for rel, path, sz, salt, page1 in db_files:
        if salt in key_map:
            result[rel] = key_map[salt]

    out = "/Users/xinjin/Desktop/vibe-coding/wechat-ai-bot/keys/wechat_keys.json"
    f = open(out, "w")
    json.dump(result, f, indent=2)
    f.close()
    print("SAVED to wechat_keys.json (%d keys)" % len(result))

process = lldb.debugger.GetSelectedTarget().GetProcess()
run(process)
