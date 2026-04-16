#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transcribe_voices.py — Extract and batch-transcribe WeChat voice messages

Usage:
  python scripts/transcribe_voices.py --name "nickname"
  python scripts/transcribe_voices.py --name "nickname" --model medium
  python scripts/transcribe_voices.py --name "nickname" --correct   # AI homophone correction

Pipeline:
  media_0.db VoiceInfo BLOB  →  strip \\x02  →  SILK decode  →  WAV  →  Whisper  →  voice_map.json
  voice_map.json  →  export_chat.py --voice-json  →  Markdown

Platform differences:
  Mac   : silk-v3-decoder compiled binary  +  ffmpeg (brew install)
  Windows: pilk Python package (pip install pilk)  —  no binary needed, WAV written natively

Dependencies:
  pip install openai-whisper zstandard anthropic
  Windows: pip install pilk
  Mac:     git clone https://github.com/kn007/silk-v3-decoder && cd silk/silk && make
           brew install ffmpeg
"""

import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import argparse, json, os, re, shutil, subprocess, tempfile, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_chat import sqlcipher_query, find_keys_file, detect_wxid_and_db_dir, search_contacts


# ── SILK → WAV (platform-specific) ───────────────────────────────

def _silk_to_wav_windows(voice_bytes: bytes) -> tuple[str | None, str]:
    """Windows: SILK → PCM via pilk, then PCM → WAV via wave module (no ffmpeg needed)."""
    try:
        import pilk
    except ImportError:
        print("[!] pilk not installed. Run: pip install pilk", flush=True)
        return None, ""

    data = voice_bytes[1:] if voice_bytes and voice_bytes[0] == 0x02 else voice_bytes
    tmp  = tempfile.mkdtemp()
    silk_path = os.path.join(tmp, "v.silk")
    pcm_path  = os.path.join(tmp, "v.pcm")
    wav_path  = os.path.join(tmp, "v.wav")

    with open(silk_path, "wb") as f:
        f.write(data)

    try:
        pilk.decode(silk_path, pcm_path)  # SILK → raw PCM (s16le, 24kHz, mono)
        if not os.path.exists(pcm_path) or os.path.getsize(pcm_path) == 0:
            return None, tmp

        import wave
        with open(pcm_path, "rb") as f:
            pcm_data = f.read()
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)   # 16-bit
            w.setframerate(24000)
            w.writeframes(pcm_data)

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            return None, tmp
    except Exception as e:
        print(f"  silk decode error: {e}", flush=True)
        return None, tmp

    return wav_path, tmp


def _find_decoder_mac() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here,  "silk-v3-decoder", "silk", "decoder"),
        os.path.join(here,  "..", "silk-v3-decoder", "silk", "decoder"),
        os.path.join(tempfile.gettempdir(), "silk-v3-decoder", "silk", "decoder"),
        os.path.expanduser("~/silk-v3-decoder/silk/decoder"),
        os.path.expanduser("~/silk-v3-decoder/decoder"),
    ]
    return next((c for c in candidates if os.path.exists(c)), None)


def _silk_to_wav_mac(voice_bytes: bytes, decoder: str) -> tuple[str | None, str]:
    """Mac: decode SILK using compiled silk-v3-decoder binary, then ffmpeg."""
    data = voice_bytes[1:] if voice_bytes and voice_bytes[0] == 0x02 else voice_bytes
    tmp  = tempfile.mkdtemp()
    silk_path = os.path.join(tmp, "v.silk")
    pcm_path  = os.path.join(tmp, "v.pcm")
    wav_path  = os.path.join(tmp, "v.wav")

    with open(silk_path, "wb") as f:
        f.write(data)

    r = subprocess.run([decoder, silk_path, pcm_path], capture_output=True, timeout=15)
    if r.returncode != 0 or not os.path.exists(pcm_path) or os.path.getsize(pcm_path) == 0:
        return None, tmp

    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
         "-i", pcm_path, wav_path],
        capture_output=True, timeout=15)
    if r.returncode != 0 or not os.path.exists(wav_path):
        return None, tmp

    return wav_path, tmp


def silk_to_wav(voice_bytes: bytes, decoder=None) -> tuple[str | None, str]:
    if sys.platform == "win32":
        return _silk_to_wav_windows(voice_bytes)
    return _silk_to_wav_mac(voice_bytes, decoder)


# ── Voice record extraction ───────────────────────────────────────

def find_media_db(db_dir: str) -> str | None:
    path = os.path.join(db_dir, "message", "media_0.db")
    return path if os.path.exists(path) else None


def get_voice_records(media_db: str, media_key: str, target_wxid: str):
    """Return (chat_name_id, total_count, [(ts, hex_voice_data), ...])."""
    rows = sqlcipher_query(media_db, media_key, "SELECT rowid, user_name FROM Name2Id;")
    chat_id = next((int(r[0]) for r in rows if r[1].strip() == target_wxid), None)
    if chat_id is None:
        return None, 0, []

    total = int((sqlcipher_query(media_db, media_key,
        f"SELECT COUNT(*) FROM VoiceInfo WHERE chat_name_id={chat_id};") or [('0',)])[0][0])

    voice_rows = sqlcipher_query(media_db, media_key,
        f"SELECT create_time, hex(voice_data) FROM VoiceInfo "
        f"WHERE chat_name_id={chat_id} ORDER BY create_time ASC;")

    return chat_id, total, [(int(r[0]), r[1]) for r in voice_rows if len(r) >= 2]


# ── AI correction ─────────────────────────────────────────────────

def correct_with_claude(voice_map: dict, api_key: str) -> dict:
    """
    Use Claude to fix Whisper homophone errors in voice_map.json.
    Corrections are stored as {"text": "original", "corrections": ["精华→清华"]}.
    Entries that already have corrections are skipped.
    """
    try:
        import anthropic
    except ImportError:
        print("[!] anthropic not installed. Run: pip install anthropic", flush=True)
        return voice_map

    client = anthropic.Anthropic(api_key=api_key)
    pending = [(k, v) for k, v in voice_map.items()
               if v.get("text") and "corrections" not in v]

    if not pending:
        print("  All entries already corrected.")
        return voice_map

    print(f"  Correcting {len(pending)} entries with Claude...\n")
    corrected = 0

    for i, (ts_key, entry) in enumerate(pending):
        text = entry["text"].strip()
        if not text or len(text) < 2:
            voice_map[ts_key]["corrections"] = []
            continue

        prompt = (
            "以下是中文语音识别结果，可能含有同音字错误（Whisper 常见问题）。\n"
            "请识别并修正明显的同音字/同音词错误，保持原句语气和意思不变。\n"
            "只修改有把握的错误，不要过度修改。\n\n"
            f"原文：{text}\n\n"
            '以 JSON 返回（如无错误，corrections 为空数组）：\n'
            '{"corrected": "修正后文本", "corrections": ["原词→正确词", ...]}'
        )
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            m = re.search(r'\{.*?\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                corr_text   = data.get("corrected", text).strip()
                corrections = [str(c) for c in data.get("corrections", []) if c]
                voice_map[ts_key]["text"]        = corr_text
                voice_map[ts_key]["corrections"] = corrections
                if corrections:
                    print(f"  [{i+1}/{len(pending)}] {corrections}", flush=True)
                    corrected += 1
                else:
                    voice_map[ts_key]["corrections"] = []
        except Exception as e:
            voice_map[ts_key]["corrections"] = []
            print(f"  [{i+1}/{len(pending)}] error: {e}", flush=True)

        # Save checkpoint every 50
        if (i + 1) % 50 == 0:
            print(f"  (checkpoint at {i+1})", flush=True)

    print(f"\n  Corrected {corrected}/{len(pending)} entries.")
    return voice_map


# ── Main ──────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Batch-transcribe WeChat voice messages")
    ap.add_argument("--name",    help="Contact nickname/remark (fuzzy search)")
    ap.add_argument("--wxid",    help="Specify wxid directly")
    ap.add_argument("--group",   action="store_true", help="Search group chats")
    ap.add_argument("--out",     default=None, help="Output JSON path (default: repo root)")
    ap.add_argument("--model",   default="small",
                    help="Whisper model: tiny/base/small/medium/large (default: small)")
    ap.add_argument("--correct", action="store_true",
                    help="Run AI homophone correction after transcription (requires ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    if not args.name and not args.wxid:
        ap.print_help()
        sys.exit(1)

    # ── Platform checks ───────────────────────────────────────────
    decoder = None
    if sys.platform == "win32":
        try:
            import pilk  # noqa: F401
        except ImportError:
            sys.exit("Error: pilk not installed. Run: pip install pilk")
    else:
        decoder = _find_decoder_mac()
        if not decoder:
            sys.exit(
                "Error: silk-v3-decoder not found. Compile it first:\n"
                f"  git clone https://github.com/kn007/silk-v3-decoder {tempfile.gettempdir()}/silk-v3-decoder\n"
                f"  cd {tempfile.gettempdir()}/silk-v3-decoder/silk && make"
            )

    # ── Load keys and DBs ─────────────────────────────────────────
    keys_file = find_keys_file()
    keys      = json.load(open(keys_file))
    _, db_dir = detect_wxid_and_db_dir(keys_file)

    media_db  = find_media_db(db_dir)
    if not media_db:
        sys.exit("Error: media_0.db not found")
    media_key = next((v for k, v in keys.items()
                      if "media_0.db" in k.replace("\\", "/")), "")
    if not media_key:
        sys.exit("Error: no key for media_0.db — re-run extract_key_windows.py")

    # ── Resolve contact ───────────────────────────────────────────
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

    print(f"Target : {display_name} ({target_wxid})")

    # ── Query voice records ───────────────────────────────────────
    print("Reading voice records from media_0.db...")
    chat_id, total, records = get_voice_records(media_db, media_key, target_wxid)
    if chat_id is None:
        sys.exit(f"Error: no voice records found for {target_wxid}")
    print(f"  {total} voice messages found\n")

    # ── Output path ───────────────────────────────────────────────
    safe     = re.sub(r'[^\w\u4e00-\u9fff]', '_', display_name)
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out      = args.out or os.path.join(repo_dir, f"{safe}_voice_map.json")

    # Load existing (supports resume)
    voice_map = {}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            voice_map = json.load(f)
        print(f"Found {len(voice_map)} existing entries — resuming\n")

    # ── Load Whisper ──────────────────────────────────────────────
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    print(f"Loading Whisper '{args.model}' model...", end="", flush=True)
    import whisper
    model = whisper.load_model(args.model)
    print(" done\n", flush=True)

    # ── Transcription loop ────────────────────────────────────────
    failed = 0
    t0 = time.time()

    for i, (ts, hex_data) in enumerate(records):
        ts_key = str(ts)
        if ts_key in voice_map and voice_map[ts_key].get("text"):
            continue  # resume

        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        try:
            wav_path, tmp = silk_to_wav(bytes.fromhex(hex_data), decoder)
            if wav_path:
                result = model.transcribe(wav_path, language=None, fp16=False,
                                          initial_prompt="以下是普通话的句子。")
                text = result["text"].strip()
                voice_map[ts_key] = {"text": text}

                done = i + 1
                eta  = int((time.time() - t0) / done * (total - done)) if done else 0
                print(f"[{done}/{total}] {dt}  {text[:80]}  (eta {eta}s)", flush=True)
            else:
                failed += 1
                voice_map[ts_key] = {"text": "[转录失败]"}
                print(f"[{i+1}/{total}] {dt}  ✗ decode failed", flush=True)

            shutil.rmtree(tmp, ignore_errors=True)

            if (i + 1) % 50 == 0:
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(voice_map, f, ensure_ascii=False, indent=2)
                print(f"  Saved {len(voice_map)} entries", flush=True)

        except Exception as e:
            failed += 1
            print(f"[{i+1}/{total}] {dt}  ✗ {e}", flush=True)

    # Final save
    with open(out, "w", encoding="utf-8") as f:
        json.dump(voice_map, f, ensure_ascii=False, indent=2)
    elapsed = int(time.time() - t0)
    print(f"\nTranscription done: {len(voice_map) - failed}/{total} succeeded, "
          f"{failed} failed, {elapsed}s")
    print(f"→ {out}")

    # ── AI correction ─────────────────────────────────────────────
    if args.correct:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            keyfile = os.path.join(repo_dir, ".apikey")
            api_key = open(keyfile).read().strip() if os.path.exists(keyfile) else ""
        if not api_key:
            print("\n[!] --correct requires ANTHROPIC_API_KEY (or .apikey file). Skipping.")
        else:
            print("\nRunning AI homophone correction...")
            voice_map = correct_with_claude(voice_map, api_key)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(voice_map, f, ensure_ascii=False, indent=2)
            print(f"Saved corrections → {out}")

    print(f"\nNext step:")
    print(f"  python scripts/export_chat.py --name \"{display_name}\" --voice-json {out}")


if __name__ == "__main__":
    main()
