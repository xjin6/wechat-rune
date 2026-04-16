"""
extract_key_windows.py — Extract WeChat PC encryption keys on Windows

How it works:
  1. Locate all .db files under %USERPROFILE%\\Documents\\WeChat Files\\
  2. Find WeChat.exe in the running process list
  3. Scan readable memory regions for the SQL key pattern:  x'<64 hex chars>'
  4. Validate each candidate against the database using HMAC verification
     (tries PBKDF2-SHA1 first, then PBKDF2-SHA512)
  5. Write matched keys to scripts/keys/wechat_keys.json

Requirements:
  pip install psutil
  Run as Administrator (required for ReadProcessMemory access)

Usage:
  python scripts\\keys\\extract_key_windows.py
"""
import ctypes
import ctypes.wintypes
import os
import sys
import re
import json
import glob
import struct
import hashlib
import hmac as hmac_mod

# ── Windows API setup ─────────────────────────────────────────────

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi    = ctypes.WinDLL('Psapi',    use_last_error=True)

PROCESS_VM_READ          = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT  = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD    = 0x100

PAGE_SZ  = 4096
KEY_SZ   = 32
SALT_SZ  = 16


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_size_t),
        ("AllocationBase",    ctypes.c_size_t),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize",        ctypes.c_size_t),
        ("State",             ctypes.wintypes.DWORD),
        ("Protect",           ctypes.wintypes.DWORD),
        ("Type",              ctypes.wintypes.DWORD),
    ]


# ── Database file collection ──────────────────────────────────────

def _find_xwechat_files() -> str:
    """Locate the xwechat_files directory on any Windows drive."""
    import ctypes, string, winreg

    # Registry first
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in (r'Software\Tencent\WeChat', r'Software\Tencent\Weixin'):
            try:
                key = winreg.OpenKey(hive, key_path)
                for val_name in ('FileSavePath', 'FileStoragePath', 'DataPath'):
                    try:
                        val, _ = winreg.QueryValueEx(key, val_name)
                        candidate = os.path.join(val, 'xwechat_files')
                        if os.path.isdir(candidate):
                            winreg.CloseKey(key)
                            return candidate
                    except OSError:
                        pass
                winreg.CloseKey(key)
            except OSError:
                pass

    # Drive search
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if not (bitmask & 1):
            bitmask >>= 1
            continue
        bitmask >>= 1
        drive = letter + ':/'
        if not os.path.exists(drive):
            continue
        for root, dirs, _ in os.walk(drive):
            dirs[:] = [d for d in dirs if d not in
                       ('Windows', '$Recycle.Bin', 'ProgramData', 'System Volume Information')]
            if 'xwechat_files' in dirs:
                return os.path.join(root, 'xwechat_files')
            if root.count(os.sep) > 5:
                dirs.clear()
    return ""


def collect_db_files() -> tuple[list, dict]:
    """
    Walk the xwechat_files directory and collect all candidate encrypted databases.
    Returns (db_files, salt_to_dbs) where:
      db_files   = [(rel_path, abs_path, size, salt_hex, page1_bytes), ...]
      salt_to_dbs = {salt_hex: [rel_path, ...]}
    """
    base = _find_xwechat_files()
    if not os.path.isdir(base):
        print(f"[!] xwechat_files not found. Set XWECHAT_FILES env var.")
        return [], {}

    db_files  = []
    salt_map  = {}

    for root, dirs, files in os.walk(base):
        for fname in files:
            if not fname.lower().endswith('.db'):
                continue
            if fname.endswith('-wal') or fname.endswith('-shm'):
                continue
            path = os.path.join(root, fname)
            try:
                sz = os.path.getsize(path)
                if sz < PAGE_SZ:
                    continue
                with open(path, 'rb') as fh:
                    page1 = fh.read(PAGE_SZ)
                if len(page1) < PAGE_SZ:
                    continue
                # First 16 bytes are the salt
                salt = page1[:SALT_SZ].hex()
                # Skip plaintext SQLite files (header "SQLite format 3")
                if page1[:16] == b'SQLite format 3\x00':
                    continue
                rel = os.path.relpath(path, base)
                db_files.append((rel, path, sz, salt, page1))
                salt_map.setdefault(salt, []).append(rel)
            except (PermissionError, OSError):
                pass

    return db_files, salt_map


# ── Key verification ──────────────────────────────────────────────

def _verify_sha1(key_bytes: bytes, page1: bytes) -> bool:
    """Verify key using PBKDF2-SHA1 + HMAC-SHA1 (Windows WeChat default).

    SQLCipher 4 page layout for HMAC-SHA1 (reserve=48, iv=16, hmac=20):
      [0:16]         salt
      [16:4064]      encrypted data (hmac input)
      [4064:4084]    HMAC-SHA1 (20 bytes)
      [4084:4096]    padding
    """
    try:
        salt     = page1[:SALT_SZ]
        mac_salt = bytes(b ^ 0x3A for b in salt)
        mac_key  = hashlib.pbkdf2_hmac('sha1', key_bytes, mac_salt, 2, dklen=KEY_SZ)

        # reserve=48, iv_sz=16 → mac_start = PAGE_SZ - 48 + 16 = 4064
        mac_start  = PAGE_SZ - 48 + 16          # 4064
        hmac_data  = page1[SALT_SZ : mac_start]  # page1[16:4064]
        stored_mac = page1[mac_start : mac_start + 20]

        h = hmac_mod.new(mac_key, hmac_data, hashlib.sha1)
        h.update(struct.pack('<I', 1))           # page number = 1
        return h.digest() == stored_mac
    except Exception:
        return False


def _verify_sha512(key_bytes: bytes, page1: bytes) -> bool:
    """Verify key using PBKDF2-SHA512 + HMAC-SHA512 (Mac / some Windows versions).

    SQLCipher 4 page layout for HMAC-SHA512 (reserve=80, iv=16, hmac=64):
      [0:16]         salt
      [16:4032]      encrypted data (hmac input)
      [4032:4096]    HMAC-SHA512 (64 bytes) — no separate IV bytes here
    """
    try:
        salt     = page1[:SALT_SZ]
        mac_salt = bytes(b ^ 0x3A for b in salt)
        mac_key  = hashlib.pbkdf2_hmac('sha512', key_bytes, mac_salt, 2, dklen=KEY_SZ)

        # reserve=80, iv_sz=16 → mac_start = PAGE_SZ - 80 + 16 = 4032
        mac_start  = PAGE_SZ - 80 + 16          # 4032
        hmac_data  = page1[SALT_SZ : mac_start]  # page1[16:4032]
        stored_mac = page1[PAGE_SZ - 64 : PAGE_SZ]

        h = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
        h.update(struct.pack('<I', 1))
        return h.digest() == stored_mac
    except Exception:
        return False


def verify_key(key_hex: str, page1: bytes) -> bool:
    """Try both HMAC algorithms. Returns True if either succeeds."""
    key_bytes = bytes.fromhex(key_hex)
    return _verify_sha1(key_bytes, page1) or _verify_sha512(key_bytes, page1)


def verify_key_open(key_hex: str, db_path: str) -> bool:
    """Fallback: actually open the database with sqlcipher3 to confirm the key."""
    for sha1 in (True, False):
        try:
            import sqlcipher3 as _sc
            conn = _sc.connect(db_path)
            conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
            conn.execute("PRAGMA cipher_page_size = 4096")
            if sha1:
                conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
                conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.close()
            return True
        except Exception:
            pass
    return False


# ── Process memory scan ───────────────────────────────────────────

def find_wechat_pid() -> int:
    """Return the PID of WeChat.exe (or 0 if not running)."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            if proc.info['name'].lower() in ('wechat.exe', 'weixin.exe'):
                return proc.info['pid']
    except ImportError:
        # psutil not installed — fall back to Windows PSAPI
        MAX_PIDS = 1024
        pids = (ctypes.wintypes.DWORD * MAX_PIDS)()
        cb_needed = ctypes.wintypes.DWORD()
        psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(cb_needed))
        count = cb_needed.value // ctypes.sizeof(ctypes.wintypes.DWORD)
        for i in range(count):
            pid = pids[i]
            h = kernel32.OpenProcess(0x0410, False, pid)   # PROCESS_QUERY_INFO | VM_READ
            if not h:
                continue
            buf = ctypes.create_unicode_buffer(260)
            psapi.GetModuleBaseNameW(h, None, buf, 260)
            kernel32.CloseHandle(h)
            if buf.value.lower() in ('wechat.exe', 'weixin.exe'):
                return pid
    return 0


def scan_process_memory(pid: int, db_files: list, salt_map: dict) -> dict[str, str]:
    """
    Scan WeChat process memory for SQL key patterns and validate them.
    Returns {rel_db_path: key_hex}.
    """
    handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not handle:
        err = ctypes.get_last_error()
        print(f"[!] OpenProcess failed (error {err}). Try running as Administrator.")
        return {}

    HEX_PAT = re.compile(rb"x'([0-9a-fA-F]{64,96})'")

    key_map: dict[str, str] = {}
    remaining_salts = set(salt_map.keys())

    # ── Enumerate readable memory regions ────────────────────────
    mbi      = MEMORY_BASIC_INFORMATION()
    addr     = 0
    regions  = []
    MAX_ADDR = (1 << 47)   # user-space limit on 64-bit Windows

    while addr < MAX_ADDR:
        sz = kernel32.VirtualQueryEx(
            handle, ctypes.c_size_t(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if sz == 0:
            break
        if (mbi.State == MEM_COMMIT
                and (mbi.Protect & PAGE_NOACCESS) == 0
                and (mbi.Protect & PAGE_GUARD)    == 0
                and 0 < mbi.RegionSize < 256 * 1024 * 1024):
            regions.append((mbi.BaseAddress, mbi.RegionSize))
        addr = mbi.BaseAddress + mbi.RegionSize
        if addr == 0:
            break

    print(f"  Scanning {len(regions)} memory regions for key pattern...")

    bytes_read  = ctypes.c_size_t(0)
    CHUNK       = 4 * 1024 * 1024  # 4 MB

    for base, total in regions:
        if not remaining_salts:
            break
        offset = 0
        while offset < total:
            read_sz = min(CHUNK, total - offset)
            buf     = (ctypes.c_char * read_sz)()
            ok = kernel32.ReadProcessMemory(
                handle,
                ctypes.c_size_t(base + offset),
                buf,
                read_sz,
                ctypes.byref(bytes_read)
            )
            offset += read_sz
            if not ok or bytes_read.value == 0:
                continue

            data = bytes(buf[:bytes_read.value])
            for m in HEX_PAT.finditer(data):
                hex_str = m.group(1).decode('ascii')
                hex_len = len(hex_str)

                if hex_len == 96:
                    # 64-char key + 32-char salt embedded in the string
                    cand_key  = hex_str[:64]
                    cand_salt = hex_str[64:]
                    if cand_salt not in remaining_salts:
                        continue
                    for rel, path, sz, db_salt, page1 in db_files:
                        if db_salt == cand_salt and verify_key(cand_key, page1):
                            key_map[rel] = cand_key
                            remaining_salts.discard(cand_salt)
                            print(f"  FOUND key for: {rel}")
                            break

                elif hex_len == 64:
                    # Bare 32-byte key — validate against every remaining database
                    cand_key = hex_str
                    for rel, path, sz, db_salt, page1 in db_files:
                        if db_salt not in remaining_salts:
                            continue
                        if verify_key(cand_key, page1):
                            key_map[rel] = cand_key
                            remaining_salts.discard(db_salt)
                            print(f"  FOUND key for: {rel}")

    kernel32.CloseHandle(handle)
    return key_map


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("WeChat Key Extractor for Windows")
    print("=" * 40)

    pid = find_wechat_pid()
    if not pid:
        print("[!] WeChat is not running. Please start WeChat and log in.")
        sys.exit(1)
    print(f"WeChat process found: PID {pid}")

    print("Collecting database files...")
    db_files, salt_map = collect_db_files()
    if not db_files:
        print("[!] No WeChat databases found. Make sure WeChat has synced messages.")
        sys.exit(1)
    print(f"Found {len(db_files)} candidate databases")

    key_map = scan_process_memory(pid, db_files, salt_map)

    if not key_map:
        print("\n[!] No keys found.")
        print("    Tip: run this script as Administrator.")
        sys.exit(1)

    # Build result with full paths as keys (matching how config.py and decrypt.py look them up)
    result = {}
    for rel, path, sz, salt, page1 in db_files:
        if rel in key_map:
            result[rel] = key_map[rel]

    out_path = os.path.join(os.path.dirname(__file__), "wechat_keys.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)

    print(f"\nSaved {len(result)} key(s) to: {out_path}")
    for rel in result:
        print(f"  {rel}")


if __name__ == '__main__':
    main()
