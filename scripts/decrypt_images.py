"""
Decrypt Weixin 4.x V2 .dat image files for a single conversation.

Algorithm ported from jackwener/wx-cli (src/attachment/decoder/v2.rs + image_key/windows.rs):
  V2 file layout:
    [6B magic 07 08 V2 08 07]
    [4B aes_size LE]
    [4B xor_size LE]
    [1B padding]              -> 15-byte header total
    [aligned_aes_size bytes]  -> AES-128-ECB ciphertext (PKCS7 padded to 16B blocks)
    [raw bytes (plain)]
    [xor_size bytes]          -> XOR with 1-byte key

Keys needed:
  - AES key (16 ASCII alphanumeric bytes): scanned from Weixin.exe process memory
  - XOR key (1 byte): derived from sample files (JPEGs end with FF D9, so
    last_byte ^ 0xD9 = xor_key for V2 files)

Run as Administrator (ReadProcessMemory needs it for reliable region access).
"""
import os
import re
import sys
import ctypes
import ctypes.wintypes
import struct
import argparse
from pathlib import Path
from collections import Counter

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

V2_MAGIC = b"\x07\x08V2\x08\x07"
V1_MAGIC = b"\x07\x08V1\x08\x07"
V1_FIXED_KEY = b"cfcd208495d565ef"  # md5("0")[:16]
HEADER_SIZE = 15

# ── Format detection ─────────────────────────────────────────────

def detect_format(data: bytes) -> str:
    if len(data) >= 4 and data[:4] == b"wxgf":
        return "hevc"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if len(data) >= 4 and data[:4] == b"\x89PNG":
        return "png"
    if len(data) >= 3 and data[:3] == b"GIF":
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 4 and data[:4] == b"II*\x00":
        return "tif"
    if len(data) >= 2 and data[:2] == b"BM":
        return "bmp"
    return "bin"

# ── V2 decoder ───────────────────────────────────────────────────

def aes_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    return cipher.decryptor().update(ciphertext) + cipher.decryptor().finalize()

def aes_ecb_decrypt_pkcs7(key: bytes, ciphertext: bytes) -> bytes:
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise ValueError(f"AES input length {len(ciphertext)} not multiple of 16")
    out = aes_ecb_decrypt(key, ciphertext)
    pad = out[-1]
    if pad == 0 or pad > 16 or pad > len(out):
        raise ValueError(f"Bad PKCS7 padding: {pad}")
    if not all(b == pad for b in out[-pad:]):
        raise ValueError("Inconsistent PKCS7 padding bytes")
    return out[:-pad]

def decode_v2(file_bytes: bytes, aes_key: bytes, xor_key: int) -> tuple[bytes, str]:
    if len(file_bytes) < HEADER_SIZE:
        raise ValueError(f"file too short: {len(file_bytes)}")
    magic = file_bytes[:6]
    if magic == V1_MAGIC:
        aes_key = V1_FIXED_KEY  # V1 uses fixed key
    elif magic != V2_MAGIC:
        raise ValueError(f"unrecognized magic: {magic.hex()}")

    aes_size = struct.unpack("<I", file_bytes[6:10])[0]
    xor_size = struct.unpack("<I", file_bytes[10:14])[0]
    aligned_aes = aes_size + (16 - aes_size % 16)
    aes_end = HEADER_SIZE + aligned_aes
    raw_end = len(file_bytes) - xor_size
    if aes_end > raw_end:
        raise ValueError(f"aes/xor overlap: aes_end={aes_end} raw_end={raw_end}")

    dec_aes = aes_ecb_decrypt_pkcs7(aes_key, file_bytes[HEADER_SIZE:aes_end])
    raw_data = file_bytes[aes_end:raw_end]
    xor_data = bytes(b ^ xor_key for b in file_bytes[raw_end:])
    out = dec_aes + raw_data + xor_data
    fmt = detect_format(out)
    if fmt == "bin":
        raise ValueError("decoded but no recognized format magic — AES key likely wrong")
    return out, fmt

# ── XOR key derivation (voting on JPEG end-marker 0xD9) ─────────

def derive_xor_key(attach_dir: Path, sample: int = 20) -> int:
    votes = []
    for path in sorted(attach_dir.rglob("*.dat")):
        try:
            data = path.read_bytes()
        except Exception:
            continue
        if len(data) < 0x20 or not data.startswith(V2_MAGIC):
            continue
        votes.append(data[-1] ^ 0xD9)
        if len(votes) >= sample:
            break
    if not votes:
        return 0x88  # documented default
    return Counter(votes).most_common(1)[0][0]

# ── Template extraction (for AES key verification) ──────────────

def collect_templates(attach_dir: Path, max_templates: int = 6) -> list[bytes]:
    """Pull the first 16B of AES ciphertext (bytes[15:31]) from a few V2 files.
    Decrypting any of these with the correct AES key yields a plaintext block
    whose first bytes are a valid image-format magic."""
    seen = set()
    out = []
    # Prefer _t.dat (thumbnails — small, faster), fall back to any .dat
    for suffix in ("_t.dat", ".dat"):
        for path in sorted(attach_dir.rglob(f"*{suffix}")):
            try:
                data = path.read_bytes()
            except Exception:
                continue
            if len(data) < 0x1F or not data.startswith(V2_MAGIC):
                continue
            tpl = bytes(data[0x0F:0x1F])
            if tpl not in seen:
                seen.add(tpl)
                out.append(tpl)
                if len(out) >= max_templates:
                    return out
        if out:
            return out
    return out

def verify_aes_key(key: bytes, templates: list[bytes]) -> bool:
    if not templates:
        return False
    for tpl in templates:
        try:
            block = aes_ecb_decrypt(key, tpl)
            if detect_format(block) == "bin":
                return False
        except Exception:
            return False
    return True

# ── Windows: scan Weixin.exe process memory for AES key ─────────

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_size_t),
        ("AllocationBase", ctypes.c_size_t),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD),
        ("Type", ctypes.wintypes.DWORD),
    ]

def find_main_weixin_pid() -> int:
    """Find the MAIN Weixin.exe process (no --type flag)."""
    import psutil
    candidates = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            if proc.info['name'].lower() == 'weixin.exe':
                cmd = proc.info.get('cmdline') or []
                has_type_flag = any('--type=' in arg for arg in cmd)
                if not has_type_flag:
                    candidates.append((proc.info['create_time'], proc.info['pid']))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not candidates:
        return 0
    candidates.sort()
    return candidates[0][1]  # earliest start time

def scan_memory_for_aes_key(pid: int, templates: list[bytes]) -> bytes | None:
    handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not handle:
        err = ctypes.get_last_error()
        print(f"[!] OpenProcess failed (error {err}). Run as Administrator.", file=sys.stderr)
        return None

    pat16 = re.compile(rb"(?:^|[^A-Za-z0-9])([A-Za-z0-9]{16})(?:[^A-Za-z0-9]|$)")
    pat32 = re.compile(rb"(?:^|[^A-Za-z0-9])([A-Za-z0-9]{32})(?:[^A-Za-z0-9]|$)")

    seen = set()
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    MAX_ADDR = (1 << 47)
    CHUNK = 4 * 1024 * 1024  # 4MB
    bytes_read = ctypes.c_size_t(0)
    regions = 0
    found = None

    while addr < MAX_ADDR:
        sz = kernel32.VirtualQueryEx(
            handle, ctypes.c_size_t(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if sz == 0:
            break
        if (mbi.State == MEM_COMMIT
                and (mbi.Protect & PAGE_NOACCESS) == 0
                and (mbi.Protect & PAGE_GUARD) == 0
                and 0 < mbi.RegionSize < 50 * 1024 * 1024):
            regions += 1
            base = mbi.BaseAddress
            total = mbi.RegionSize
            offset = 0
            while offset < total and found is None:
                read_sz = min(CHUNK, total - offset)
                buf = (ctypes.c_char * read_sz)()
                ok = kernel32.ReadProcessMemory(
                    handle, ctypes.c_size_t(base + offset),
                    buf, read_sz, ctypes.byref(bytes_read)
                )
                offset += read_sz
                if not ok or bytes_read.value == 0:
                    continue
                data = bytes(buf[:bytes_read.value])
                # Try 32-char then 16-char
                for pat, take in ((pat32, 16), (pat16, 16)):
                    for m in pat.finditer(data):
                        cand = m.group(1)[:16]
                        if cand in seen:
                            continue
                        seen.add(cand)
                        if verify_aes_key(cand, templates):
                            found = cand
                            break
                    if found:
                        break
            if found:
                break
        addr = mbi.BaseAddress + mbi.RegionSize
        if addr == 0:
            break

    kernel32.CloseHandle(handle)
    print(f"  Scanned {regions} regions, {len(seen)} candidates")
    return found

# ── Main ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach-dir", required=True, help="Per-contact attach folder (md5(wxid))")
    ap.add_argument("--out-dir", required=True, help="Output folder for decrypted images")
    ap.add_argument("--aes-key", help="Hex AES key (skip memory scan)")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N files (0 = all)")
    ap.add_argument("--probe", action="store_true", help="Only decrypt 1 file as a smoke test")
    args = ap.parse_args()

    attach_dir = Path(args.attach_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {attach_dir}...")
    templates = collect_templates(attach_dir)
    print(f"  Templates: {len(templates)}")
    if not templates:
        print("[!] No V2 templates found; nothing to decrypt.")
        return 1

    xor_key = derive_xor_key(attach_dir)
    print(f"  XOR key: 0x{xor_key:02x}")

    if args.aes_key:
        aes_key = bytes.fromhex(args.aes_key) if len(args.aes_key) == 32 else args.aes_key.encode()
        if not verify_aes_key(aes_key, templates):
            print("[!] Provided AES key failed verification.")
            return 1
        print(f"  AES key verified (provided): {aes_key.decode('ascii', 'replace')}")
    else:
        pid = find_main_weixin_pid()
        if not pid:
            print("[!] Main Weixin.exe not found. Is Weixin running?")
            return 1
        print(f"  Scanning Weixin.exe PID {pid} for AES key...")
        aes_key = scan_memory_for_aes_key(pid, templates)
        if not aes_key:
            print("[!] AES key not found in memory. Run as Admin / restart Weixin.")
            return 1
        print(f"  AES key: {aes_key.decode('ascii')}")

    # Decrypt all .dat files
    dat_files = sorted(attach_dir.rglob("*.dat"))
    print(f"\nDecrypting {len(dat_files)} files...")
    ok = 0
    failed = []
    for i, src in enumerate(dat_files, 1):
        if args.limit and i > args.limit:
            break
        try:
            data = src.read_bytes()
            out, fmt = decode_v2(data, aes_key, xor_key)
            rel = src.relative_to(attach_dir)
            dst = out_dir / rel.with_suffix(f".{fmt}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(out)
            ok += 1
            if args.probe:
                print(f"  PROBE: {src.name} -> {dst.name} ({fmt}, {len(out)} bytes)")
                break
        except Exception as e:
            failed.append((src.name, str(e)))
        if i % 100 == 0:
            print(f"  {i}/{len(dat_files)}: ok={ok} fail={len(failed)}")

    print(f"\nDone. Decrypted {ok}/{len(dat_files)}. Failed: {len(failed)}")
    for name, err in failed[:10]:
        print(f"  FAIL {name}: {err}")

if __name__ == "__main__":
    sys.exit(main() or 0)
