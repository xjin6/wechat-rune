---
name: wechat-setup
description: >
  Step-by-step guide to sync, decrypt, and export WeChat (微信) chat history on macOS,
  including voice message transcription (语音转文字) via Whisper, then optionally set up
  an AI auto-reply bot powered by Claude.
  Trigger this skill whenever the user wants to: export WeChat chat records to markdown,
  decrypt the WeChat database, extract WeChat encryption keys, transcribe WeChat voice
  messages, set up a WeChat AI bot, or asks anything about accessing local WeChat data on Mac.
---

# WeChat Chat History Export & AI Bot Setup Guide

Four steps. Wait for the user to confirm completion of each step before proceeding.

| Step | Description | Core Script |
|------|-------------|-------------|
| Step 1 | Sync phone chat history to Mac | (Manual) |
| Step 2 | Extract database encryption keys | `scripts/keys/extract_key2.py` |
| Step 3 | Export chat history + voice transcription | `scripts/export_chat.py` + `scripts/transcribe_voices.py` |
| Step 4 | Set up AI auto-reply bot | `scripts/start.py` |

---

## Step 1: Sync Phone Chat History to Mac

> Skip if you only need records already on the Mac.

1. Log in to WeChat on Mac
2. Phone: WeChat → Settings → General → Chat History Migration & Backup → Migrate to Desktop WeChat
3. Select chats, same Wi-Fi, scan QR code

---

## Step 2: Extract Database Encryption Keys

WeChat databases are encrypted with SQLCipher. Keys are **bound to account + device** — re-extract after switching devices or major WeChat upgrades. One extraction covers all 18 databases.

### Prerequisites

```bash
brew install sqlcipher ffmpeg
pip install -r requirements.txt

# SILK decoder (for voice transcription in Step 3)
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make
```

### Extraction

Run from the **repo root directory**:

```bash
# 1. Re-sign WeChat (allow debugger to attach)
sudo codesign --force --deep --sign - /Applications/WeChat.app

# 2. WeChat must be running and logged in
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"

# 3. Verify
python3 -c "import json; print(len(json.load(open('scripts/keys/wechat_keys.json'))), 'keys')"
```

> ~30-60 seconds. Recommend reinstalling WeChat from App Store afterward to restore original signature.

### Permission Issues

- macOS 13+: System Settings → Privacy & Security → Developer Tools → enable Terminal
- Still fails: temporarily disable SIP (Recovery Mode → `csrutil disable`, re-enable when done)

---

## Step 3: Export Chat History

### Basic Export

Ask: "Whose chat history? Nickname or remark name? Private or group chat?"

```bash
python3 scripts/export_chat.py --name "nickname"           # private chat
python3 scripts/export_chat.py --name "group name" --group  # group chat
python3 scripts/export_chat.py --name "nickname" --out path.md  # custom output
```

Defaults to repo root directory (gitignored). Multiple matches → script prompts user to choose.

### Voice Transcription (Optional)

Voice data lives in `media_0.db` as SILK BLOBs — no network download needed.

```bash
# Transcribe
python3 scripts/transcribe_voices.py --name "nickname"

# Re-export with transcriptions
python3 scripts/export_chat.py --name "nickname" --voice-json name_voice_map.json
```

Supports resumable runs, real-time progress, auto-save every 50 entries.
Option: `--model medium` for higher accuracy (default `small`, ~2.8s/entry).

### Voice Correction (Optional)

Whisper may produce homophone errors. After transcription, use an LLM to correct obvious mistakes in `voice_map.json`. Corrections are appended as HTML comments (invisible in rendered Markdown, visible to AI):

```
[语音 10s] 来清华玩 <!-- correction: 精华→清华 -->
```

### Supported Message Types

Square brackets = type + attributes. Outside = content.

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
| Chat Record | `[聊天记录] title` |
| Transfer | `[转账 ¥0.68]` |
| Red Packet | `[红包 note]` |
| Link | `[链接] title` |
| Call | `[语音通话 120s]` / `[视频通话 未接通]` |
| Pat-pat | `[互动] I tickled JJWang's ...` |
| Quote Reply | text + `> quoted` (nested, with sender + time) |
| System | *italic* (recalled, friend added, red packet opened) |

---

## Step 4 (Optional): AI Auto-Reply Bot

### Setup

Enable accessibility: System Settings → Privacy & Security → Accessibility → enable Terminal

Guide the user interactively — do not ask them to edit files manually:

1. **Conversations**: Ask who to monitor, look up wxid, write to `.watch`
2. **API Key**: Ask for Anthropic API Key (`sk-ant-...`), write to `.apikey`
3. **Trigger words**: Customize via `.env` (default trigger words are in `config.py`)

### Launch

```bash
python3 scripts/start.py
```

Optional dashboard: `python3 scripts/dashboard.py` (localhost:7788)

Stop: `Ctrl+C`

---

## Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| No key in `wechat_keys.json` | Re-extract (Step 2) |
| Empty export | Check wxid |
| LLDB attach fails | Developer Tools permission, or disable SIP |
| AppleScript can't send | Accessibility permission |
| Sender reversed | Re-extract keys |
| Voice in Traditional Chinese | `initial_prompt="以下是普通话的句子。"` |
| No key for `media_0.db` | Re-extract while WeChat is running |
| SILK decode fails | Strip `\x02` prefix |
| `KMP_DUPLICATE_LIB_OK` error | `export KMP_DUPLICATE_LIB_OK=TRUE` |
| DB corruption dialog | Cancel to ignore, or Repair + re-extract keys |
| Articles show as `[链接]` | Use `local_type % 65536 = 49` in SQL |

---

## Technical Reference

### Database Structure

| Database | Contents |
|----------|----------|
| `message_N.db` | Chat messages (table = `Msg_` + MD5(wxid)) |
| `contact.db` | Contacts (nick_name, remark, wxid) |
| `media_0.db` | Voice data (`VoiceInfo` table, SILK BLOBs) |
| `message_resource.db` | Media resource index |
| Other 14 | Stickers, favorites, Moments, etc. |

### type=49 Subtypes

`local_type=49` has flag bits in high bits. Match with `local_type % 65536 == 49`. Inner `<type>` distinguishes:

| `<type>` | Meaning | Key Field |
|----------|---------|-----------|
| 5 | Official account | `<sourcedisplayname>` |
| 6 | File | `<title>` = filename |
| 8 | Sticker | — |
| 19 | Merged forward | `<title>` + `<des>` |
| 33, 36 | Mini program | `<sourcedisplayname>` |
| 57 | Quote reply | `<refermsg>` |
| 62 | Pat-pat (拍一拍) | `<title>` |
| 2000 | Transfer | `<feedesc>` = amount |
| 2001 | Red packet | `<sendertitle>` = note |

### Sender Identification

`Name2Id.rowid` → `real_sender_id`. Display names from `contact.db` `nick_name` (not remark).

### Deduplication

`server_id` (globally unique from WeChat server), not text comparison.

### Quote Reply Recursion

`<refermsg>` → `<content>` may nest XML. Recursive parse with `<displayname>` + `<createtime>` metadata per level. `<fromusr>` falls back to nick_map. Non-text quotes keep attributes (`[语音 4s]`, `[视频 90s]`).

### Voice Pipeline

```
media_0.db VoiceInfo.voice_data (BLOB)
  → strip \x02
  → silk-v3-decoder → PCM (s16le, 24kHz, mono)
  → ffmpeg → WAV
  → Whisper (language=None, initial_prompt="以下是普通话的句子。")
  → voice_map.json
  → export_chat.py --voice-json → Markdown
```

---

## Script Index

| Script | Purpose | Step |
|--------|---------|------|
| `scripts/keys/extract_key2.py` | Extract SQLCipher keys via LLDB | 2 |
| `scripts/export_chat.py` | Export chat to Markdown | 3 |
| `scripts/transcribe_voices.py` | Batch voice transcription | 3 |
| `scripts/start.py` | Launch AI bot | 4 |
| `scripts/bot.py` | Bot main process (DB monitor + Claude) | 4 |
| `scripts/config.py` | Config loader (env, API key, triggers) | 4 |
| `scripts/dashboard.py` | Web dashboard (localhost:7788) | 4 |
