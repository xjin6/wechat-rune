[English](README.md) | [中文](README_CN.md)

# wechat-export-bot

Export WeChat / Weixin chat history to Markdown (with voice transcription and AI correction), and optionally run an AI auto-reply bot — fully local, no cloud required.

> **macOS and Windows.** Requires WeChat / Weixin desktop client.

---

## Features

### Chat Export
- Export any private chat or group chat to clean Markdown
- **16 message types** supported: text, images, voice, video, stickers, official accounts, mini programs, files, transfers, red packets, pat-pat, quotes (nested), calls, system messages
- Voice messages automatically transcribed via Whisper (extracted directly from local DB, no network needed)
- AI homophone correction with invisible HTML annotations
- Sender names displayed as original WeChat nicknames
- Deduplication via `server_id` (globally unique)

### AI Bot
- Monitors specified conversations for trigger words
- Replies automatically via Claude API
- Mac: sends via AppleScript — no unofficial protocols
- Windows: sends via Win32 keyboard simulation
- Per-conversation memory with configurable history window
- RAG support with vector search for long-term context

---

## How It Works

**Chat Export:**
```
Encrypted DB → SQLCipher decrypt → parse messages → Markdown
Voice (Mac):     media_0.db BLOB → silk-v3-decoder → ffmpeg → Whisper
Voice (Windows): media_0.db BLOB → pilk → numpy resample → Whisper
```

**AI Bot:**
```
DB change detected → SQLCipher query → trigger match → Claude API → send reply
```

---

## Quick Start

### Prerequisites

**Mac:**
```bash
brew install sqlcipher ffmpeg
pip install -r requirements.txt

# SILK decoder (for voice transcription)
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make
```

**Windows:**
```bash
pip install -r requirements.txt
# Run subsequent commands as Administrator
```

### 1. Extract encryption keys

**Mac** (WeChat must be running):
```bash
sudo codesign --force --deep --sign - /Applications/WeChat.app
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"
```

**Windows** (run as Administrator, Weixin must be running):
```bash
python scripts\keys\extract_key_windows.py
```

### 2. Export chat history

```bash
python3 scripts/export_chat.py --name "nickname"
python3 scripts/export_chat.py --name "group name" --group
```

### 3. Transcribe voice messages (optional)

```bash
python3 scripts/transcribe_voices.py --name "nickname"
# Claude corrects homophone errors automatically (no extra command needed)
python3 scripts/export_chat.py --name "nickname" --voice-json name_voice_map.json
```

### 4. Run AI bot (optional)

```bash
echo "wxid_xxx" > .watch
echo "sk-ant-xxx" > .apikey
python3 scripts/start.py
```

---

## Supported Message Types

| Type | Output |
|------|--------|
| Text | `hi你好。` |
| Image | `[图片]` |
| Voice | `[语音 9s] transcribed text` |
| Video | `[视频 23s]` |
| Sticker | `[表情包]` |
| Official Account | `[公众号 \| Name] title` |
| Mini Program | `[小程序 \| Name] title` |
| File | `[文件] file.pdf` |
| Chat Record | `[聊天记录] title` |
| Transfer | `[转账 ¥0.68]` |
| Red Packet | `[红包 note]` |
| Pat-pat | `[互动] I tickled ...` |
| Call | `[语音通话 120s]` |
| Quote Reply | text + `> quoted` (nested) |
| System | *italic* |

---

## Project Structure

```
wechat-export-bot/
├── scripts/
│   ├── export_chat.py           # Chat history → Markdown
│   ├── transcribe_voices.py     # Voice → Whisper transcription (Mac + Windows)
│   ├── start.py                 # Bot launcher
│   ├── bot.py                   # Bot main process
│   ├── config.py                # Configuration — auto-detects platform
│   ├── dashboard.py             # Web dashboard (localhost:7788)
│   ├── keys/
│   │   ├── extract_key2.py      # Mac: LLDB key extraction
│   │   └── extract_key_windows.py  # Windows: Win32 memory scan
│   └── core/                    # AI, contacts, RAG, embeddings, sender, etc.
├── SKILL.md                     # Claude Code skill definition
├── requirements.txt
└── .gitignore
```

---

## Platform Comparison

| | Mac | Windows |
|---|---|---|
| Key extraction | LLDB + Mach API | ReadProcessMemory Win32 |
| SILK decode | silk-v3-decoder (compiled) | pilk (pip) |
| Audio | ffmpeg → WAV | numpy resample → array |
| Bot send | AppleScript | Win32 keybd_event |
| Data path | `~/Library/Containers/.../xwechat_files` | Auto-detected via registry / drive scan |

Database schema, message parsing, AI logic, and RAG are identical on both platforms.

---

## Security

- `keys/wechat_keys.json` — gitignored, never commit
- Exported chat files — gitignored
- API keys — environment variables only
- Mac: restore WeChat signature after key extraction

---

## Limitations

- WeChat / Weixin desktop client must be running
- Keys must be re-extracted after major WeChat updates
- Voice transcription: ~2.8s per message (Whisper `small` model)
- Bot sending requires WeChat window to be open (Windows)
