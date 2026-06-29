"""Sync exported chat records to the Obsidian wiki as IMAGE-LESS markdown.

The Vibe workspace (`relationship/<label>/<label>_wechat.md`) is the source of
truth and keeps the full version WITH inline image embeds + the `images/` folder.
The Obsidian wiki should hold a lightweight copy: image *descriptions* kept
(`[图片: …]` text), but the actual `![](images/…)` embeds stripped and no
`images/` folder — so the vault doesn't carry tens/hundreds of MB of pictures.

This is the standard final step after any (re-)export. It is idempotent.

What it does, per contact:
  1. Read  <vibe>/<label>/<label>_wechat.md   (full, with embeds)
  2. Delete every standalone `![](images/…)` line (descriptions stay)
  3. Write <wiki>/<label>/<label>_wechat.md    (no-image version)
  4. Remove <wiki>/<label>/images/             (unless --keep-images)

It NEVER touches hand-curated files (context / analysis / ledger) or the Vibe
workspace. Image embeds in this pipeline are always on their own line (verified
across both contacts), so line-deletion is exact — no inline embeds exist.

Usage:
  python scripts/sync_to_wiki.py --vibe-root <relationship> --wiki-root <wiki>   # all contacts
  python scripts/sync_to_wiki.py <label> [<label> ...] --vibe-root … --wiki-root …
  python scripts/sync_to_wiki.py --keep-images   # sync text but leave images

Roots come from --vibe-root / --wiki-root, or the env vars WECHAT_VIBE_ROOT /
WECHAT_WIKI_ROOT. Nothing is hardcoded to a specific machine or contact.
"""
import os, re, sys, glob, time, shutil, stat, argparse

# No hardcoded paths/names — supply via --vibe-root/--wiki-root or these env vars.
DEFAULT_VIBE = os.environ.get("WECHAT_VIBE_ROOT", "")
DEFAULT_WIKI = os.environ.get("WECHAT_WIKI_ROOT", "")

EMBED_LINE = re.compile(r"^!\[\]\(images/[^)]*\)\s*$")


def purge_dir(path: str) -> str:
    """Delete a directory tree, resilient to OneDrive transient locks.

    OneDrive often holds a brief handle on a just-emptied folder, making the
    rmdir fail with WinError 5 even after every file is gone. Retry a few times,
    clearing read-only bits, and report honestly if empty skeletons linger
    (files — i.e. actual disk space — are removed regardless)."""
    if not os.path.isdir(path):
        return "absent"

    def onexc(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    for _ in range(4):
        shutil.rmtree(path, onexc=onexc)
        if not os.path.exists(path):
            return "purged"
        time.sleep(0.7)
    files_left = sum(len(fs) for _, _, fs in os.walk(path))
    return f"files purged (empty dirs linger, {files_left} files left — OneDrive lock)"


def detect_labels(vibe_root: str) -> list:
    labels = []
    for md in glob.glob(os.path.join(vibe_root, "*", "*_wechat.md")):
        label = os.path.basename(os.path.dirname(md))
        if os.path.basename(md) == f"{label}_wechat.md":
            labels.append(label)
    return sorted(labels)


def strip_embeds(text: str) -> tuple:
    lines = text.split("\n")
    kept = [ln for ln in lines if not EMBED_LINE.match(ln)]
    return "\n".join(kept), len(lines) - len(kept)


def sync_contact(label: str, vibe_root: str, wiki_root: str, purge_images: bool) -> None:
    src = os.path.join(vibe_root, label, f"{label}_wechat.md")
    if not os.path.exists(src):
        print(f"  [{label}] SKIP — no source md at {src}")
        return
    text = open(src, encoding="utf-8").read()
    cleaned, removed = strip_embeds(text)

    dst_dir = os.path.join(wiki_root, label)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"{label}_wechat.md")
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(cleaned)

    imgs = cleaned.count("![](images/")
    descs = cleaned.count("[图片:")
    anim = cleaned.count("[动图")
    size_kb = os.path.getsize(dst) // 1024
    note = ""
    if purge_images:
        status = purge_dir(os.path.join(dst_dir, "images"))
        if status != "absent":
            note = f" | images: {status}"
    leftover = " ⚠ EMBEDS REMAIN" if imgs else ""
    print(f"  [{label}] {size_kb} KB | -{removed} embed lines | 图片描述={descs} 动图={anim}{note}{leftover}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", nargs="*", help="Contact labels (default: auto-detect)")
    ap.add_argument("--vibe-root", default=DEFAULT_VIBE)
    ap.add_argument("--wiki-root", default=DEFAULT_WIKI)
    ap.add_argument("--keep-images", action="store_true",
                    help="Do not delete the wiki images/ folder")
    args = ap.parse_args()

    if not args.vibe_root or not args.wiki_root:
        sys.exit("Set --vibe-root and --wiki-root (or env WECHAT_VIBE_ROOT / "
                 "WECHAT_WIKI_ROOT). No paths are hardcoded.")

    labels = args.labels or detect_labels(args.vibe_root)
    if not labels:
        sys.exit(f"No contacts found under {args.vibe_root}")
    print(f"Syncing no-image version -> {args.wiki_root}")
    for label in labels:
        sync_contact(label, args.vibe_root, args.wiki_root, purge_images=not args.keep_images)


if __name__ == "__main__":
    main()
