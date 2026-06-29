"""harvest_edits.py — pull manual edits made in the Wiki .md back into the JSON
sources (image_descriptions.json, voice_map.json), so they survive regeneration.

The user reads/edits the image-less Wiki .md in Obsidian. Both .md files are fully
regenerated on every update, so hand-edits would be lost. This harvests them:

  1. A freshly-rendered BASELINE (export_chat output, embeds stripped) carries an
     ordered anchor list: the md5 / voice-ts for each image / voice line, in render
     order (export_chat --emit-anchors).
  2. The user's CURRENT Wiki .md is aligned to the baseline with difflib. Unedited
     messages (the bulk) match exactly and anchor the alignment; new records show as
     baseline-only inserts; an edited description/transcript shows as a 1:1 line
     'replace'.
  3. For each clean 1:1 replace of an image/voice line, the user's text is harvested
     to JSON[key]. ANYTHING ambiguous (block lengths differ, type mismatch, anchor
     desync) is NOT applied — it's reported for you to confirm. Never silently
     mis-assign.

Harvested keys are flagged manual (voice in-band; images in a registry) so AI
description / correction never overwrites them.

Usage:
  python scripts/harvest_edits.py --user-wiki <wiki .md> --baseline-vibe <vibe .md> \
    --anchors <anchors.json> --image-descriptions <…> --voice-map <…> \
    [--manual-registry <…>] [--apply]
Without --apply it only reports (dry run).
"""
import argparse, difflib, json, os, re, sys

IMG_RE   = re.compile(r"^\[(?:图片|动图)(?::\s*(.*?))?\]$")          # group1 = desc or None
EMBED_RE = re.compile(r"^!\[\]\(images/[^)]*\)\s*$")


def is_img(line):
    return bool(IMG_RE.match(line))


def is_voice(line):
    # voice MESSAGE "[语音 9s] …"  (NOT a call "[语音通话…]" / "[视频通话…]")
    return line.startswith("[语音 ")


def img_desc(line):
    m = IMG_RE.match(line)
    return (m.group(1) or "").strip() if m else None


def voice_text(line):
    # "[语音 9s] transcript <!-- 纠正: … -->"  ->  transcript (comment stripped)
    body = re.sub(r"^\[语音 [^\]]*\]\s*", "", line)
    body = re.sub(r"\s*<!--.*?-->\s*$", "", body)
    return body.strip()


def strip_embeds(text):
    return [ln for ln in text.split("\n") if not EMBED_RE.match(ln)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-wiki", required=True)
    ap.add_argument("--baseline-vibe", required=True)
    ap.add_argument("--anchors", required=True)
    ap.add_argument("--image-descriptions", required=True)
    ap.add_argument("--voice-map", required=True)
    ap.add_argument("--manual-registry", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    base_lines = strip_embeds(open(args.baseline_vibe, encoding="utf-8").read())
    user_lines = open(args.user_wiki, encoding="utf-8").read().split("\n")
    anchors = json.load(open(args.anchors, encoding="utf-8"))

    # map each baseline image/voice line index -> its anchor; verify type sync
    base_anchor = {}
    ai = 0
    for idx, ln in enumerate(base_lines):
        if is_img(ln) or is_voice(ln):
            if ai >= len(anchors):
                sys.exit(f"[abort] more image/voice lines than anchors at line {idx} — regen mismatch")
            exp = "img" if is_img(ln) else "voice"
            if anchors[ai]["type"] != exp:
                sys.exit(f"[abort] anchor/line type desync at baseline line {idx}: "
                         f"anchor={anchors[ai]['type']} line={exp} — not harvesting")
            base_anchor[idx] = anchors[ai]
            ai += 1

    harvested = []      # (kind, key, old, new)
    ambiguous = []      # human-readable notes

    sm = difflib.SequenceMatcher(None, base_lines, user_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "insert"):   # insert = new records only in baseline... (baseline=a)
            continue
        if tag == "delete":               # lines only in baseline (e.g. new records) — skip
            continue
        # tag == "replace": base[i1:i2] <-> user[j1:j2]
        if (i2 - i1) != (j2 - j1):
            # uneven block — only flag the image/voice baseline lines in it
            for bi in range(i1, i2):
                if bi in base_anchor:
                    a = base_anchor[bi]
                    ambiguous.append(f"line~{bi} ({a['type']} {str(a['key'])[:12]}): "
                                     f"uneven replace block, not auto-applied")
            continue
        for k in range(i2 - i1):
            bi, uj = i1 + k, j1 + k
            if bi not in base_anchor:
                continue
            a = base_anchor[bi]
            bline, uline = base_lines[bi], user_lines[uj]
            # type must still match on the user side
            if a["type"] == "img":
                if not is_img(uline):
                    ambiguous.append(f"img {str(a['key'])[:12]}: paired user line is not an image line")
                    continue
                old, new = img_desc(bline), img_desc(uline)
                if new is None or new == old:
                    continue
                if a["key"] is None:
                    ambiguous.append(f"img edit but baseline line has no md5 anchor: {new[:40]}")
                    continue
                harvested.append(("img", a["key"], old, new))
            else:  # voice
                if not is_voice(uline):
                    ambiguous.append(f"voice {a['key']}: paired user line is not a voice line")
                    continue
                old, new = voice_text(bline), voice_text(uline)
                if not new or new == old:
                    continue
                harvested.append(("voice", a["key"], old, new))

    print(f"=== harvest report ===")
    print(f"  baseline image/voice lines: {len(base_anchor)} | anchors: {len(anchors)}")
    print(f"  edits to harvest: {len(harvested)} | ambiguous (NOT applied): {len(ambiguous)}")
    for kind, key, old, new in harvested:
        print(f"  [{kind}] {str(key)[:14]}")
        print(f"      old: {(old or '')[:70]}")
        print(f"      new: {new[:70]}")
    for note in ambiguous:
        print(f"  ⚠ {note}")

    if not args.apply:
        print("\n(dry run — re-run with --apply to write)")
        return
    if not harvested:
        print("\nnothing to apply.")
        return

    descs = json.load(open(args.image_descriptions, encoding="utf-8"))
    vmap = json.load(open(args.voice_map, encoding="utf-8"))
    reg_path = args.manual_registry
    reg = {"images": [], "voices": []}
    if reg_path and os.path.exists(reg_path):
        reg = json.load(open(reg_path, encoding="utf-8"))
    for kind, key, old, new in harvested:
        if kind == "img":
            descs[key] = new
            if key not in reg["images"]:
                reg["images"].append(key)
        else:
            entry = vmap.get(key) or {}
            entry["text"] = new
            entry["manual"] = True
            entry.pop("corrections", None)   # user's text supersedes any auto-correction
            vmap[key] = entry
            if key not in reg["voices"]:
                reg["voices"].append(key)
    json.dump(descs, open(args.image_descriptions, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(vmap, open(args.voice_map, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if reg_path:
        json.dump(reg, open(reg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\napplied {len(harvested)} edit(s) to JSON sources"
          + (f" + flagged manual in {os.path.basename(reg_path)}" if reg_path else ""))


if __name__ == "__main__":
    main()
