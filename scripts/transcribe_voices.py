#!/usr/bin/env python3
"""
transcribe_voices.py — Extract voice messages from media_0.db and batch-transcribe with Whisper

Usage:
  python3 scripts/transcribe_voices.py --name "王行健"
  python3 scripts/transcribe_voices.py --name "王行健" --model medium
  python3 scripts/transcribe_voices.py --name "王行健" --correct   # post-transcription correction

How it works:
  WeChat 4.x stores voice data (SILK format) directly in the VoiceInfo table of media_0.db.
  Pipeline: media_0.db BLOB → strip \x02 → silk decoder → PCM → ffmpeg → WAV → Whisper

Dependencies:
  pip install openai-whisper zstandard
  brew install ffmpeg
  git clone https://github.com/kn007/silk-v3-decoder.git && cd silk-v3-decoder/silk && make
"""

import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime

# ── Import utility functions from export_chat ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_chat import (sqlcipher_query, find_keys_file,
                         detect_wxid_and_db_dir, search_contacts)

# ── Locate silk decoder ──────────────────────────────────────────────────────

def find_decoder():
    here = os.path.dirname(os.path.abspath(__file__))
    for c in [
        os.path.join(here, "silk-v3-decoder", "silk", "decoder"),
        os.path.join(here, "..", "silk-v3-decoder", "silk", "decoder"),
        "/tmp/silk-v3-decoder/silk/decoder",
        os.path.expanduser("~/silk-v3-decoder/silk/decoder"),
        os.path.expanduser("~/silk-v3-decoder/decoder"),
    ]:
        if os.path.exists(c):
            return c
    return None

# ── Locate media_0.db ────────────────────────────────────────────────────────

def find_media_db(db_dir):
    """Find media_0.db under the message directory."""
    msg_dir = os.path.join(db_dir, "message")
    path = os.path.join(msg_dir, "media_0.db")
    return path if os.path.exists(path) else None

# ── Voice extraction ─────────────────────────────────────────────────────────

def get_voice_records(media_db, media_key, target_wxid):
    """Query voice records for the target contact from media_0.db.
    Returns (chat_name_id, total_count, [(ts, hex_voice_data), ...]).
    """
    # Find chat_name_id
    rows = sqlcipher_query(media_db, media_key,
        "SELECT rowid, user_name FROM Name2Id;")
    chat_id = None
    for r in rows:
        if r[1].strip() == target_wxid:
            chat_id = int(r[0])
            break
    if chat_id is None:
        return None, 0, []

    count_row = sqlcipher_query(media_db, media_key,
        f"SELECT COUNT(*) FROM VoiceInfo WHERE chat_name_id={chat_id};")
    total = int(count_row[0][0]) if count_row else 0

    voice_rows = sqlcipher_query(media_db, media_key,
        f"SELECT create_time, hex(voice_data) FROM VoiceInfo "
        f"WHERE chat_name_id={chat_id} ORDER BY create_time ASC;")

    records = []
    for row in voice_rows:
        if len(row) >= 2:
            records.append((int(row[0]), row[1]))
    return chat_id, total, records

# ── Audio processing ─────────────────────────────────────────────────────────

def silk_to_wav(voice_hex, decoder):
    """hex voice_data -> WAV file path. Returns (None, tmp_dir) on failure."""
    voice_bytes = bytes.fromhex(voice_hex)
    tmp = tempfile.mkdtemp()

    # Write SILK (strip \x02 prefix)
    silk_path = os.path.join(tmp, "v.silk")
    with open(silk_path, "wb") as f:
        data = voice_bytes[1:] if voice_bytes and voice_bytes[0] == 0x02 else voice_bytes
        f.write(data)

    # SILK → PCM
    pcm_path = os.path.join(tmp, "v.pcm")
    r = subprocess.run([decoder, silk_path, pcm_path],
                       capture_output=True, timeout=15)
    if r.returncode != 0 or not os.path.exists(pcm_path) or os.path.getsize(pcm_path) == 0:
        return None, tmp

    # PCM → WAV
    wav_path = os.path.join(tmp, "v.wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
         "-i", pcm_path, wav_path],
        capture_output=True, timeout=15)
    if r.returncode != 0 or not os.path.exists(wav_path):
        return None, tmp

    return wav_path, tmp

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Batch transcribe WeChat voice messages (from media_0.db)")
    ap.add_argument("--name", help="Contact remark/nickname (fuzzy search)")
    ap.add_argument("--wxid", help="Specify wxid directly")
    ap.add_argument("--group", action="store_true", help="Search group chats")
    ap.add_argument("--out", default=None, help="Output JSON path (default: repo root)")
    ap.add_argument("--model", default="small",
                    help="Whisper model: tiny/base/small/medium/large (default: small)")
    ap.add_argument("--correct", action="store_true",
                    help="Post-transcription homophone correction (requires a manual correction table)")
    args = ap.parse_args()

    if not args.name and not args.wxid:
        ap.print_help()
        sys.exit(1)

    # Check dependencies
    decoder = find_decoder()
    if not decoder:
        sys.exit(
            "Error: silk-v3-decoder not found. Please compile it first:\n"
            "   git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder\n"
            "   cd /tmp/silk-v3-decoder/silk && make"
        )

    # Find keys and DB
    keys_file = find_keys_file()
    keys = json.load(open(keys_file))
    _, db_dir = detect_wxid_and_db_dir(keys_file)

    media_db = find_media_db(db_dir)
    if not media_db:
        sys.exit("Error: media_0.db not found")
    media_key = next((v for k, v in keys.items() if "media_0.db" in k), "")
    if not media_key:
        sys.exit("Error: no key for media_0.db in wechat_keys.json — please re-extract keys")

    # Determine target contact
    if args.wxid:
        target_wxid, display_name = args.wxid, args.wxid
    else:
        results = search_contacts(keys_file, db_dir, args.name, group=args.group)
        if not results:
            sys.exit(f"Error: contact '{args.name}' not found")
        if len(results) == 1:
            target_wxid, display_name = results[0]
        else:
            print("Multiple contacts found:")
            for i, (w, n) in enumerate(results):
                print(f"  [{i+1}] {n} ({w})")
            idx = int(input("Select number: ")) - 1
            target_wxid, display_name = results[idx]

    print(f"Target: {display_name} ({target_wxid})")

    # Query voice records
    print("Reading voice records from media_0.db...")
    chat_id, total, records = get_voice_records(media_db, media_key, target_wxid)
    if chat_id is None:
        sys.exit(f"Error: no voice records for {target_wxid} in media_0.db")
    print(f"  {total} voice messages total\n")

    # Load Whisper
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    print(f"Loading Whisper {args.model} model...", end="", flush=True)
    import whisper
    model = whisper.load_model(args.model)
    print(" ✅\n", flush=True)

    # Output path
    safe = re.sub(r'[^\w\u4e00-\u9fff]', '_', display_name)
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = args.out or os.path.join(repo_dir, f"{safe}_voice_map.json")

    # Load existing results (supports resume from checkpoint)
    voice_map = {}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            voice_map = json.load(f)
        print(f"Found {len(voice_map)} existing entries, skipping completed\n")

    # Batch transcription
    failed = 0
    t0 = time.time()

    for i, (ts, hex_data) in enumerate(records):
        ts_key = str(ts)
        if ts_key in voice_map:
            continue  # Resume: skip already completed

        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        try:
            wav_path, tmp = silk_to_wav(hex_data, decoder)
            if wav_path:
                result = model.transcribe(wav_path, language=None, fp16=False,
                                          initial_prompt="以下是普通话的句子。")
                text = result["text"].strip()
                voice_map[ts_key] = {"text": text}

                eta = int((time.time() - t0) / (i + 1) * (total - i - 1))
                print(f"[{i+1}/{total}] {dt} → {text[:80]}  ({eta}s)", flush=True)
            else:
                failed += 1
                print(f"[{i+1}/{total}] {dt} ✗ decode failed", flush=True)

            shutil.rmtree(tmp, ignore_errors=True)

            # Save every 50 entries
            if (i + 1) % 50 == 0:
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(voice_map, f, ensure_ascii=False, indent=2)
                print(f"  Saved {len(voice_map)} entries", flush=True)

        except Exception as e:
            failed += 1
            print(f"[{i+1}/{total}] {dt} ✗ {e}", flush=True)

    # Final save
    with open(out, "w", encoding="utf-8") as f:
        json.dump(voice_map, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    print(f"\nDone! {len(voice_map)}/{total} succeeded, {failed} failed, took {int(elapsed)}s")
    print(f"→ {out}")
    print(f"\nNext step: python3 scripts/export_chat.py --name \"{display_name}\" --voice-json {out}")


if __name__ == "__main__":
    main()
