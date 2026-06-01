"""
Derive Weixin 4.x V2 image AES key by brute-forcing uin.

Constraint (from wx-cli/src/attachment/image_key/macos.rs derive_image_key_material):
  xor_key = uin & 0xFF
  aes_key = md5(str(uin) + wxid).hexdigest()[:16].encode()  # ASCII bytes

We already know:
  - xor_key = derived from .dat files (last byte ^ 0xD9 voting)
  - wxid suffix = the 4 hex chars from the account folder name (e.g., "c092" in "magicxinjx_c092")
    and md5(str(uin)).hexdigest()[:4] should == that suffix

So search space is uin where (uin & 0xFF == xor_key) AND (md5(str(uin))[:4] == suffix).
That's 2^24 candidates filtered to ~256 by the md5 prefix check.
"""
import hashlib
import sys
import argparse
from pathlib import Path
from collections import Counter
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

V2_MAGIC = b"\x07\x08V2\x08\x07"

def detect_format(data: bytes) -> str:
    if len(data) >= 4 and data[:4] == b"wxgf": return "hevc"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff": return "jpg"
    if len(data) >= 4 and data[:4] == b"\x89PNG": return "png"
    if len(data) >= 3 and data[:3] == b"GIF": return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "webp"
    if len(data) >= 4 and data[:4] == b"II*\x00": return "tif"
    if len(data) >= 2 and data[:2] == b"BM": return "bmp"
    return "bin"

def collect_templates(attach_dir: Path, max_templates: int = 6) -> list[bytes]:
    seen = set()
    out = []
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
        return 0x88
    return Counter(votes).most_common(1)[0][0]

def verify_aes_key(key: bytes, templates: list[bytes]) -> bool:
    if not templates:
        return False
    for tpl in templates:
        try:
            cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
            block = cipher.decryptor().update(tpl) + cipher.decryptor().finalize()
            if detect_format(block) == "bin":
                return False
        except Exception:
            return False
    return True

def normalize_wxid(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("wxid_"):
        head = raw[5:].split("_")[0]
        return f"wxid_{head}"
    if "_" in raw:
        base, suf = raw.rsplit("_", 1)
        if len(suf) == 4 and all(c in "0123456789abcdef" for c in suf.lower()):
            return base
    return raw

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach-dir", required=True)
    ap.add_argument("--account-folder", required=True,
                    help="Like 'magicxinjx_c092' — the folder under xwechat_files/")
    args = ap.parse_args()

    attach_dir = Path(args.attach_dir)
    folder = args.account_folder.strip()

    # Extract 4-hex suffix
    if "_" not in folder:
        print("[!] Account folder must end with _xxxx (4 hex chars)")
        return 1
    raw_wxid, suffix = folder.rsplit("_", 1)
    if len(suffix) != 4 or not all(c in "0123456789abcdef" for c in suffix.lower()):
        print(f"[!] Suffix '{suffix}' is not 4 hex chars")
        return 1
    suffix_lc = suffix.lower()
    norm_wxid = normalize_wxid(folder)

    print(f"Account folder: {folder}")
    print(f"  raw wxid:        {raw_wxid}")
    print(f"  normalized wxid: {norm_wxid}")
    print(f"  hex suffix:      {suffix_lc}")

    templates = collect_templates(attach_dir)
    print(f"\nTemplates: {len(templates)}")
    if not templates:
        print("[!] No V2 templates")
        return 1
    xor_key = derive_xor_key(attach_dir)
    print(f"XOR key: 0x{xor_key:02x}  (= uin & 0xFF)")

    # Brute-force uin
    # uin & 0xFF == xor_key, so try upper = 0..2^24, uin = (upper << 8) | xor_key
    print(f"\nBrute-forcing uin (2^24 candidates, filter by md5 prefix == {suffix_lc})...")
    wxid_candidates = [norm_wxid, raw_wxid] if norm_wxid != raw_wxid else [norm_wxid]
    hits = []
    total = 1 << 24
    for upper in range(total):
        uin = (upper << 8) | xor_key
        digest_uin = hashlib.md5(str(uin).encode()).hexdigest()
        if digest_uin[:4] != suffix_lc:
            continue
        for w in wxid_candidates:
            aes_hex = hashlib.md5(f"{uin}{w}".encode()).hexdigest()
            aes_key = aes_hex[:16].encode()
            if verify_aes_key(aes_key, templates):
                print(f"\n  FOUND: uin={uin}  wxid_for_md5={w}")
                print(f"         aes_key={aes_key.decode()}")
                print(f"         xor_key=0x{xor_key:02x}")
                return 0
            hits.append((uin, w, aes_key))
        if upper % (1 << 20) == 0 and upper > 0:
            print(f"  scanned {upper:>10} / {total}  candidates_passed_prefix={len(hits)}")

    print(f"\n[!] No AES key matched ({len(hits)} suffix candidates tested)")
    return 1

if __name__ == "__main__":
    sys.exit(main())
