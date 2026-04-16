---
name: wechat-export-bot
description: >
  Step-by-step guide to sync, decrypt, and export WeChat (微信/Weixin) chat history on
  macOS or Windows, including voice message transcription (语音转文字) via Whisper and
  AI homophone correction, then optionally set up an AI auto-reply bot powered by Claude.
  Trigger this skill whenever the user wants to: export WeChat chat records to markdown,
  decrypt the WeChat database, extract WeChat encryption keys, transcribe WeChat voice
  messages, correct voice transcriptions, set up a WeChat AI bot, or asks anything about
  accessing local WeChat data on Mac or Windows.
---

# WeChat Chat History Export & AI Bot Setup Guide

Works on **macOS** and **Windows** from the same codebase. Four steps.
**Detect platform automatically. Guide interactively — wait for user confirmation at each step.**

| Step | Description |
|------|-------------|
| 1 | Sync phone chat history to computer |
| 2 | Extract database encryption keys |
| 3 | Export chat + voice transcription + AI correction |
| 4 | (Optional) AI auto-reply bot |

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

### Windows

Run directly — no Administrator required:

```bash
python scripts/keys/extract_key_windows.py
```

**If it returns "No keys found":**
1. Completely close Weixin
2. Reopen Weixin and wait for it to fully load (log in if needed)
3. Run the script again within ~30 seconds of Weixin starting

```bash
# Verify
python -c "import json; print(len(json.load(open('scripts/keys/wechat_keys.json'))), 'keys')"
```

> Expected: 15–20 keys. If you recently synced from phone and a new `message_N.db`
> was created, that database's key will only be captured on the next Weixin restart.

### Mac

```bash
# Install dependencies (first time only)
brew install sqlcipher ffmpeg
pip install -r requirements.txt
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make

# Re-sign WeChat to allow debugger
sudo codesign --force --deep --sign - /Applications/WeChat.app

# Extract (WeChat must be running)
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"
```

**Mac permission issues:**
- macOS 13+: System Settings → Privacy & Security → Developer Tools → enable Terminal
- Still fails: Recovery Mode → `csrutil disable` → re-enable when done

---

## Step 3: Export Chat History

This step is interactive. Follow this flow:

### 3a. Basic export first

Ask: "Whose chat history would you like to export? (nickname, remark name, or group name)"

```bash
python scripts/export_chat.py --name "nickname"              # private chat
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

## Step 4 (Optional): AI Auto-Reply Bot

### Windows Setup
WeChat window must remain open; bot uses keyboard simulation to send replies.

### Mac Setup
Enable accessibility: System Settings → Privacy & Security → Accessibility → enable Terminal

### Configuration

1. **Conversations**: Ask who to monitor, look up wxid, write to `.watch`
2. **API Key**: Ask for Anthropic API Key (`sk-ant-...`), write to `.apikey`
3. **Trigger words**: Customize via `.env` (defaults in `config.py`)

### Launch

```bash
python scripts/start.py
# or specify directly:
python scripts/start.py "group name" "contact name"
```

Optional dashboard: `python scripts/dashboard.py` → http://localhost:7788 | Stop: `Ctrl+C`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No keys found" on Windows | Close Weixin fully → reopen → run script within 30s |
| "No keys found" after phone sync | New database created — must restart Weixin to capture new key |
| Empty export | Re-extract keys; check wxid spelling |
| LLDB attach fails (Mac) | Developer Tools permission, or disable SIP |
| `sqlcipher3` errors | `pip install sqlcipher3` |
| AppleScript can't send (Mac) | Accessibility permission for Terminal |
| Bot can't send (Windows) | WeChat window must be open and focused |
| Voice in Traditional Chinese | Change `initial_prompt` in `transcribe_voices.py` |
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
| `scripts/keys/extract_key_windows.py` | Windows: extract keys (run after Weixin restart) |
| `scripts/keys/extract_key2.py` | Mac: extract keys via LLDB |
| `scripts/export_chat.py` | Export chat to Markdown |
| `scripts/transcribe_voices.py` | Batch voice transcription |
| `scripts/start.py` | Launch AI bot |
| `scripts/dashboard.py` | Web dashboard — localhost:7788 |
