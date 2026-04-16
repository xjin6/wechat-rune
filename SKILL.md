---
name: wechat-setup
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
Wait for the user to confirm completion of each step before proceeding.

| Step | Description | Mac script | Windows script |
|------|-------------|-----------|----------------|
| 1 | Sync phone chat history to computer | (Manual) | (Manual) |
| 2 | Extract database encryption keys | `keys/extract_key2.py` (via LLDB) | `keys/extract_key_windows.py` |
| 3 | Export chat + voice transcription + correction | `export_chat.py` + `transcribe_voices.py` | Same |
| 4 | AI auto-reply bot | `start.py` | Same |

---

## Step 1: Sync Phone Chat History

> Skip if you only need records already on the computer.

**Mac & Windows:**
1. Log in to WeChat / Weixin on the computer
2. Phone → WeChat → Settings → General → Chat History Migration & Backup → Migrate to Desktop
3. Select chats, same Wi-Fi, scan QR code

---

## Step 2: Extract Database Encryption Keys

WeChat databases are encrypted with SQLCipher. Re-extract after switching devices or major WeChat upgrades.

### Mac

```bash
# Install dependencies
brew install sqlcipher ffmpeg
pip install -r requirements.txt

# Compile SILK decoder (for voice transcription in Step 3)
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make

# Re-sign WeChat to allow debugger
sudo codesign --force --deep --sign - /Applications/WeChat.app

# Extract (WeChat must be running and logged in)
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"

# Verify
python3 -c "import json; print(len(json.load(open('scripts/keys/wechat_keys.json'))), 'keys')"
```

> ~30–60 seconds. Reinstall WeChat from App Store afterward to restore original signature.

**Mac permission issues:**
- macOS 13+: System Settings → Privacy & Security → Developer Tools → enable Terminal
- Still fails: Recovery Mode → `csrutil disable` → re-enable when done

### Windows

```bash
# Install dependencies
pip install -r requirements.txt

# Extract (run as Administrator; Weixin must be running and logged in)
python scripts\keys\extract_key_windows.py

# Verify
python -c "import json; print(len(json.load(open('scripts/keys/wechat_keys.json'))), 'keys')"
```

> ~5–30 seconds depending on memory size.

---

## Step 3: Export Chat History

### Basic Export

Ask: "Whose chat history? Nickname or remark name? Private or group chat?"

```bash
python3 scripts/export_chat.py --name "nickname"              # private chat
python3 scripts/export_chat.py --name "group name" --group   # group chat
python3 scripts/export_chat.py --name "nickname" --out path.md
```

Defaults to repo root (gitignored). Multiple matches → script prompts to choose.
Display names use the contact's own WeChat nickname (not your personal remark).

### Voice Transcription (Optional)

Voice data lives in `media_0.db` as SILK BLOBs — no network download needed.

```bash
# Transcribe all voices for a contact
python3 scripts/transcribe_voices.py --name "nickname"
python3 scripts/transcribe_voices.py --name "nickname" --model medium   # higher accuracy

# Re-export with transcriptions embedded
python3 scripts/export_chat.py --name "nickname" --voice-json nickname_voice_map.json
```

Supports resumable runs, real-time progress, checkpoint every 50 entries.

**Voice pipeline comparison:**

| Stage | Mac | Windows |
|-------|-----|---------|
| SILK decode | `silk-v3-decoder` compiled binary | `pilk` pip package |
| Audio conversion | ffmpeg → WAV file | numpy resample → float32 array |
| Whisper input | file path | numpy array (no ffmpeg needed) |

### Voice Correction (Claude does this directly — no extra command)

After transcription, Claude reads `voice_map.json`, identifies and fixes homophone errors
(Whisper common mistakes in Mandarin), and writes corrections back inline.
Corrections appear as HTML comments in the final Markdown — invisible when rendered,
visible to AI:

```
[语音 10s] 来清华玩 <!-- correction: 精华→清华 -->
```

### Supported Message Types

| Type | Output |
|------|--------|
| Text | plain text |
| Image | `[图片]` |
| Voice | `[语音 9s] transcribed text` |
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
| System | *italic* (recalled, red packet opened, etc.) |

---

## Step 4 (Optional): AI Auto-Reply Bot

### Mac Setup

Enable accessibility: System Settings → Privacy & Security → Accessibility → enable Terminal

### Windows Setup

WeChat window must remain open; bot uses keyboard simulation to send replies.

### Configuration (both platforms)

1. **Conversations**: Ask who to monitor, look up wxid, write to `.watch`
2. **API Key**: Ask for Anthropic API Key (`sk-ant-...`), write to `.apikey`
3. **Trigger words**: Customize via `.env` (defaults in `config.py`)

### Launch

```bash
python3 scripts/start.py
# or specify conversations directly:
python3 scripts/start.py "group name" "contact name"
```

Optional dashboard: `python3 scripts/dashboard.py` → http://localhost:7788

Stop: `Ctrl+C`

---

## Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| No keys extracted | Re-extract (must run as Admin on Windows / with LLDB on Mac) |
| Empty export | Check wxid; re-extract keys while WeChat is running |
| LLDB attach fails (Mac) | Developer Tools permission, or disable SIP |
| WinError 2 in transcription | Handled automatically via numpy bypass |
| `sqlcipher3` errors | `pip install sqlcipher3` |
| AppleScript can't send (Mac) | Accessibility permission for Terminal |
| Bot can't send (Windows) | WeChat window must be open |
| Voice in Traditional Chinese | Change `initial_prompt` in `transcribe_voices.py` |
| No key for `media_0.db` | Re-extract while WeChat is running |
| DB corruption dialog | Cancel to ignore, or Repair + re-extract |
| Sender reversed | Re-extract keys |

---

## Technical Reference

### Database Structure (identical on Mac and Windows)

| Database | Contents |
|----------|----------|
| `message_N.db` | Chat messages — table `Msg_` + MD5(wxid) |
| `contact.db` | Contacts (nick_name, remark, wxid) |
| `media_0.db` | Voice data — `VoiceInfo` table, SILK BLOBs |
| Other | Stickers, favorites, Moments, etc. |

### Data Path Detection

| Platform | Method |
|----------|--------|
| Mac | Fixed: `~/Library/Containers/com.tencent.xinWeChat/.../xwechat_files` |
| Windows | Registry (`FileSavePath`) → common paths → full drive search for `xwechat_files` |

Override either platform with `XWECHAT_FILES` environment variable.

### Key Extraction

| | Mac | Windows |
|---|---|---|
| Method | LLDB debugger + Mach kernel API | ReadProcessMemory Win32 API |
| Requires | SIP disabled or Developer Tools | Administrator privileges |
| Memory pattern | `x'<64 hex chars>'` | Same |
| HMAC verification | SHA-512 | SHA-512 (SHA-1 fallback) |

### Sender

| | Mac | Windows |
|---|---|---|
| Clipboard | `pbcopy` | ctypes `CF_UNICODETEXT` |
| Send | AppleScript (`osascript`) | `keybd_event` (Ctrl+V, Enter) |

### Sender Identification

`Name2Id.rowid` → `real_sender_id`. Display names from `contact.db` `nick_name` (not remark).

---

## Script Index

| Script | Purpose | Step |
|--------|---------|------|
| `scripts/keys/extract_key2.py` | Mac: extract keys via LLDB | 2 |
| `scripts/keys/extract_key_windows.py` | Windows: extract keys via Win32 memory scan | 2 |
| `scripts/export_chat.py` | Export chat to Markdown | 3 |
| `scripts/transcribe_voices.py` | Batch voice transcription (Mac + Windows) | 3 |
| `scripts/start.py` | Launch AI bot | 4 |
| `scripts/bot.py` | Bot main process | 4 |
| `scripts/dashboard.py` | Web dashboard — localhost:7788 | 4 |
