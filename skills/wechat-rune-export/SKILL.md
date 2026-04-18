---
name: wechat-rune-export
description: >
  Export WeChat (微信/Weixin) chat history to Markdown on macOS or Windows,
  including voice message transcription (语音转文字) via Whisper and AI
  homophone correction. Walks the user through syncing from phone,
  extracting SQLCipher encryption keys from process memory, decrypting
  the databases, and producing a clean per-conversation Markdown file.
  Trigger this skill whenever the user wants to: export WeChat chat
  records, decrypt the WeChat database, back up their WeChat history,
  extract WeChat encryption keys, transcribe WeChat voice messages,
  correct voice transcriptions, or asks anything about reading local
  WeChat data on Mac or Windows.
---

# WeChat Chat History Export

Works on **macOS** and **Windows** from the same codebase. Three steps.
**Detect platform automatically. Guide interactively — wait for user confirmation at each step.**

> **Workspace convention.** All file paths and shell commands below are relative to the
> repo root (default `~/Desktop/vibe-coding/wechat-rune`). Before running any command,
> `cd` there first.

| Step | Description |
|------|-------------|
| 1 | Sync phone chat history to computer |
| 2 | Extract database encryption keys |
| 3 | Export chat + voice transcription + AI correction |

---

## Step 1: Sync Phone Chat History

Ask the user: "Have you already synced messages from your phone, or do you need to do that first?"

**How to sync:**
1. Log in to WeChat / Weixin on the computer
2. Phone → WeChat → Settings → General → Chat History Migration & Backup → Migrate to Desktop
3. Select chats, same Wi-Fi, scan QR code

> ⚠️ **Important:** After the phone sync completes, new messages may have created a new
> database shard (`message_1.db`, `message_2.db`, etc.). Each new database has its own
> encryption key. You **must** restart WeChat/Weixin and immediately run key extraction
> (Step 2) before the keys leave memory. Do not skip this even if you extracted keys before.

---

## Step 2: Extract Database Encryption Keys

WeChat databases are encrypted with SQLCipher. Keys live in process memory only briefly
after Weixin opens its databases. **Extract immediately after launch.**

**First: check whether the key file already exists and is still valid.**

```bash
python -c "
import json, os
p = 'scripts/keys/wechat_keys.json'
if not os.path.exists(p):
    print('MISSING'); exit()
d = json.load(open(p))
print(f'EXISTS: {len(d)} keys')
"
```

If it prints `EXISTS: N keys` (N > 0) and the user hasn't just done a phone sync, skip the
rest of Step 2 and jump to Step 3. Otherwise, run the platform-specific extraction below.

### Windows

Run directly — no Administrator required:

```bash
python scripts/keys/extract_key_windows.py
```

**If it returns "No keys found":**
1. Completely close Weixin
2. Reopen Weixin and wait for it to fully load (log in if needed)
3. Run the script again within ~30 seconds of Weixin starting

> Expected: 15–20 keys. If you recently synced from phone and a new `message_N.db`
> was created, that database's key will only be captured on the next Weixin restart.

### Mac

```bash
# Install dependencies (first time only)
brew install sqlcipher ffmpeg
pip install -r requirements.txt
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make

# Re-sign WeChat (one-time; must be fully quit first, then reopened after).
# Hardened runtime on the Tencent-signed binary blocks task_for_pid until
# this is done.
sudo codesign --force --deep --sign - /Applications/WeChat.app

# Build the key extractor (one-time)
cc -O2 -o scripts/keys/extract_key_macos \
    scripts/keys/extract_key_macos.c -framework Foundation

# Extract (WeChat must be running, with at least one chat opened so its
# DBs are loaded). Root is required because task_for_pid at root reaches
# the SQLCipher heap regions that hold the x'KEY+SALT' pattern; an
# ordinary-user LLDB attach cannot see those regions on WeChat 4.x.
sudo ./scripts/keys/extract_key_macos
```

> Expected: ~18 keys (one per active DB shard). The output file
> `scripts/keys/wechat_keys.json` is written alongside the binary in flat
> format, identical to what the Windows extractor produces — so every
> downstream script is cross-platform from here on.

> **Don't wrap in `osascript ... with administrator privileges`.** macOS
> sandboxes processes launched via `AuthorizationExecuteWithPrivileges`,
> and `task_for_pid` is blocked in that context. A direct terminal `sudo`
> is the only invocation that works.

---

## Step 3: Export Chat History

This step is interactive. Follow this flow:

### 3a. Basic export first

Ask: "Whose chat history would you like to export? (nickname, remark name, or group name)"

```bash
python scripts/export_chat.py --name "nickname"             # private chat
python scripts/export_chat.py --name "group name" --group   # group chat
```

Show the user the result — number of messages and file size. Let them see the Markdown file.
This gives them immediate confirmation that it's working before the longer voice step.

### 3b. Voice transcription (ask user)

After the basic export, ask: "There are N voice messages. Want to transcribe them?
It takes ~3–4 seconds per message (Whisper `small` model), so roughly X minutes total.
Use `--model medium` for higher accuracy but ~2× slower."

```bash
python scripts/transcribe_voices.py --name "nickname"
# or for higher accuracy:
python scripts/transcribe_voices.py --name "nickname" --model medium
```

Run this as a background task. **Report progress to the user periodically** — show them
sample transcribed lines every 50 entries so they know it's working.

### 3c. AI correction (do this automatically — no user input needed)

After transcription completes, immediately correct the voice_map.json by **reading the
actual content in batches of 50 entries**. Do NOT rely on a pre-defined error list —
that approach only catches patterns seen before and misses anything new.

For each batch:
1. Read the transcribed text as natural language
2. Ask: does each sentence make semantic sense in Chinese?
3. Fix words that are phonetically plausible mishears (same sound, wrong character)
4. Common categories to watch for — but always judge from context, not from this list:
   - Proper nouns: school names, app names, product names, company names
   - Technical terms: 用研 (UXR), 选修课, 劳动课, specific tools/platforms
   - Audio noise at end of long recordings (repeated characters like GGGG…)

Write corrections back with `"corrections": ["wrong→right"]` field. Then re-export:

```bash
python scripts/export_chat.py --name "nickname" --voice-json nickname_voice_map.json
```

### Supported Message Types

| Type | Output |
|------|--------|
| Text | plain text |
| Image | `[图片]` |
| Voice | `[语音 9s] transcribed text <!-- correction: X→Y -->` |
| Video | `[视频 23s]` |
| Sticker | `[表情包]` |
| Official Account | `[公众号 \| Name] title` |
| Mini Program | `[小程序 \| Name] title` |
| File | `[文件] filename` |
| Transfer | `[转账 ¥0.68]` |
| Red Packet | `[红包 note]` |
| Link | `[链接] title` |
| Call | `[语音通话 120s]` / `[视频通话 未接通]` |
| Quote Reply | text + `> quoted` (nested, with sender + time) |
| System | *italic* |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No keys found" on Windows | Close Weixin fully → reopen → run script within 30s |
| "No keys found" after phone sync | New database created — must restart Weixin to capture new key |
| `task_for_pid failed` (Mac) | WeChat not ad-hoc signed — rerun `sudo codesign --force --deep --sign - /Applications/WeChat.app` with WeChat **fully quit** first |
| `extract_key_macos` returns 0 keys (Mac) | Forgot `sudo`, or WeChat hasn't opened a chat yet — click a conversation, then rerun |
| Empty export | Re-extract keys; check wxid spelling |
| `sqlcipher3` errors | `pip install sqlcipher3` |
| Voice in Traditional Chinese | Change `initial_prompt` in `scripts/transcribe_voices.py` |
| No key for `media_0.db` | Re-extract immediately after Weixin restarts |

---

## Technical Reference

### Key Extraction Timing

Keys appear in memory as `x'<64 hex chars>'` SQL strings only when Weixin executes
`PRAGMA key` on database open. This happens once per database per session. Scan window
is roughly 30–60 seconds after Weixin starts. After phone sync, new database shards
(`message_1.db`, etc.) require a fresh Weixin restart to capture their keys.

### Database Structure

| Database | Contents |
|----------|----------|
| `message_N.db` | Chat messages — table `Msg_` + MD5(wxid). New shards created when current hits ~30MB |
| `contact.db` | Contacts (nick_name, remark, wxid) |
| `media_0.db` | Voice data — `VoiceInfo` table, SILK BLOBs |

### Voice Pipeline

| Stage | Mac | Windows |
|-------|-----|---------|
| SILK decode | `silk-v3-decoder` compiled binary | `pilk` pip package |
| Audio conversion | ffmpeg → WAV file | numpy resample → float32 array (no ffmpeg needed) |
| Whisper input | file path | numpy array |

### Script Index

| Script | Purpose |
|--------|---------|
| `scripts/keys/extract_key_windows.py` | Windows: extract keys |
| `scripts/keys/extract_key_macos.c` | Mac: extract keys (compile once, run with sudo) |
| `scripts/export_chat.py` | Export chat to Markdown |
| `scripts/transcribe_voices.py` | Batch voice transcription |
