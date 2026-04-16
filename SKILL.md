---
name: wechat-setup-windows
description: >
  Step-by-step guide to sync, decrypt, and export WeChat (微信/Weixin) chat history on Windows,
  including voice message transcription (语音转文字) via Whisper and AI correction, then optionally
  set up an AI auto-reply bot powered by Claude.
  Trigger this skill whenever the user wants to: export WeChat chat records to markdown,
  decrypt the WeChat database, extract WeChat encryption keys, transcribe WeChat voice
  messages, set up a WeChat AI bot, or asks anything about accessing local WeChat data on Windows.
---

# WeChat Chat History Export & AI Bot Setup Guide (Windows)

Four steps. Wait for the user to confirm completion of each step before proceeding.

| Step | Description | Core Script |
|------|-------------|-------------|
| Step 1 | Sync phone chat history to PC | (Manual) |
| Step 2 | Extract database encryption keys | `scripts\keys\extract_key_windows.py` |
| Step 3 | Export chat history + voice transcription + correction | `scripts\export_chat.py` + `scripts\transcribe_voices.py` |
| Step 4 | Set up AI auto-reply bot | `scripts\start.py` |

---

## Step 1: Sync Phone Chat History to PC

> Skip if you only need records already on the PC.

1. Log in to Weixin on PC
2. Phone: WeChat → Settings → General → Chat History Migration & Backup → Migrate to Desktop
3. Select chats, same Wi-Fi, scan QR code

---

## Step 2: Extract Database Encryption Keys

WeChat databases are encrypted with SQLCipher. Keys are extracted from Weixin.exe process memory.
One extraction covers all databases for the current account.

### Prerequisites

```bash
pip install -r requirements.txt
# Run as Administrator (required for ReadProcessMemory)
```

### Extraction

Weixin must be running and logged in:

```bash
# Run as Administrator
python scripts\keys\extract_key_windows.py

# Verify
python -c "import json; print(len(json.load(open('scripts/keys/wechat_keys.json'))), 'keys')"
```

> ~5-30 seconds depending on memory size.

### Permission Issues

- Must run terminal as Administrator
- If Weixin is not found: make sure it's running and logged in
- Re-extract after major Weixin updates

---

## Step 3: Export Chat History

### Basic Export

Ask: "Whose chat history? Nickname or remark name? Private or group chat?"

```bash
python scripts\export_chat.py --name "nickname"              # private chat
python scripts\export_chat.py --name "group name" --group   # group chat
python scripts\export_chat.py --name "nickname" --out path.md  # custom output
```

Defaults to repo root directory (gitignored). Multiple matches → script prompts user to choose.
Display names use the contact's own WeChat nickname (not your personal remark).

### Voice Transcription (Optional)

Voice data lives in `media_0.db` as SILK BLOBs — no network download needed.

```bash
# Transcribe (Windows: uses pilk + numpy, no ffmpeg required)
python scripts\transcribe_voices.py --name "nickname"

# Re-export with transcriptions
python scripts\export_chat.py --name "nickname" --voice-json nickname_voice_map.json
```

Supports resumable runs, real-time progress, auto-save every 50 entries.
Option: `--model medium` for higher accuracy (default `small`).

### Voice Correction (Part of this skill — no extra command needed)

After transcription, Claude reads `voice_map.json` directly and corrects obvious homophone errors
(same音字, mishears, audio noise). Corrections are stored inline and rendered as HTML comments
in the final Markdown (invisible when rendered, visible to AI):

```
[语音 10s] 来清华玩 <!-- correction: 精华→清华 -->
```

### Supported Message Types

| Type | Output Example |
|------|---------------|
| Text | `hi你好。` |
| Image | `[图片]` |
| Voice | `[语音 9s] transcribed text` |
| Video | `[视频 23s]` |
| Sticker | `[表情包]` |
| Official Account | `[公众号 \| AccountName] article title` |
| Mini Program | `[小程序 \| AppName] title` |
| File | `[文件] filename.pdf` |
| Transfer | `[转账 ¥0.68]` |
| Red Packet | `[红包 note]` |
| Link | `[链接] title` |
| Call | `[语音通话 120s]` / `[视频通话 未接通]` |
| Quote Reply | text + `> quoted` (nested, with sender + time) |
| System | *italic* (recalled, red packet opened, etc.) |

---

## Step 4 (Optional): AI Auto-Reply Bot

### Setup

1. **Conversations**: Ask who to monitor, add to `.watch` file
2. **API Key**: Ask for Anthropic API Key (`sk-ant-...`), write to `.apikey`
3. **Trigger words**: Customize via `.env` (default trigger words in `config.py`)

### Launch

```bash
python scripts\start.py
```

Optional dashboard: `python scripts\dashboard.py` (localhost:7788)

Stop: `Ctrl+C`

---

## Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| No key in `wechat_keys.json` | Re-extract (run as Administrator) |
| Empty export | Check wxid; verify keys were extracted while Weixin was running |
| Key extraction finds 0 keys | Weixin must be running and logged in |
| WinError 2 in transcription | Normal — handled automatically via numpy bypass |
| `sqlcipher3` errors | Run: `pip install sqlcipher3` |
| Voice in Traditional Chinese | Change `initial_prompt` in transcribe_voices.py |
| No key for `media_0.db` | Re-extract while Weixin is running |
| Bot can't send messages | WeChat window must be open and focused on target chat |
| Dashboard shows 0 vectors | Run `start.py` first to vectorize history |

---

## Technical Reference

### Database Structure (same as Mac)

| Database | Contents |
|----------|----------|
| `message_N.db` | Chat messages (table = `Msg_` + MD5(wxid)) |
| `contact.db` | Contacts (nick_name, remark, wxid) |
| `media_0.db` | Voice data (`VoiceInfo` table, SILK BLOBs) |
| Other | Stickers, favorites, Moments, etc. |

### Data Location

Windows: auto-detected via registry → common paths → full drive search for `xwechat_files\`
Default: `%USERPROFILE%\Documents\WeChat Files\<wxid>_xxx\db_storage\`

### Voice Pipeline Comparison

| Stage | Mac | Windows |
|-------|-----|---------|
| SILK decode | `silk-v3-decoder` (compiled C binary) | `pilk` (pip package) |
| Audio conversion | `ffmpeg` → WAV file | `numpy` → float32 array @ 16kHz |
| Whisper input | file path | numpy array (bypasses ffmpeg) |
| ffmpeg needed | Yes (`brew install ffmpeg`) | No (imageio-ffmpeg optional) |

### Sender Identification

`Name2Id.rowid` → `real_sender_id`. Display names from `contact.db` `nick_name` (not remark).

### Key Extraction Method

| Mac | Windows |
|-----|---------|
| LLDB debugger + Mach kernel API | `ReadProcessMemory` Win32 API |
| Requires SIP disabled or dev tools | Requires Administrator privileges |
| Scans for `x'<64 hex>'` pattern in memory | Same pattern scan |
| HMAC-SHA512 verification | HMAC-SHA512 + SHA1 fallback |

---

## Script Index

| Script | Purpose | Step |
|--------|---------|------|
| `scripts\keys\extract_key_windows.py` | Extract SQLCipher keys via Win32 memory scan | 2 |
| `scripts\export_chat.py` | Export chat to Markdown | 3 |
| `scripts\transcribe_voices.py` | Batch voice transcription | 3 |
| `scripts\start.py` | Launch AI bot | 4 |
| `scripts\bot.py` | Bot main process (DB monitor + Claude) | 4 |
| `scripts\config.py` | Config loader (env, API key, triggers) | 4 |
| `scripts\dashboard.py` | Web dashboard (localhost:7788) | 4 |
