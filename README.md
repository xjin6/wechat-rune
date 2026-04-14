[English](README.md) | [中文](README_CN.md)

# wechat-export-bot

Export WeChat chat history to Markdown (with voice transcription), and optionally run an AI auto-reply bot — all from your Mac, fully local.

> **macOS only.** Requires WeChat Mac desktop client.

---

## Features

### Chat Export
- Export any private chat or group chat to clean Markdown
- **16 message types** supported: text, images, voice, video, stickers, official accounts, mini programs, files, transfers, red packets, pat-pat, quotes (nested), calls, system messages
- Voice messages automatically transcribed via Whisper (extracted directly from local DB, no network needed)
- Homophone correction via LLM with invisible HTML annotations
- Sender names displayed as WeChat nicknames
- Deduplication via `server_id` (globally unique)

### AI Bot
- Monitors specified conversations for trigger words
- Replies automatically via Claude API
- Sends via AppleScript — no unofficial WeChat protocols
- Per-conversation memory with configurable history window
- RAG support with vector search for long-term context

---

## How It Works

**Chat Export:**
```
Encrypted DB → SQLCipher decrypt → parse messages → format Markdown
Voice: media_0.db BLOB → SILK decode → ffmpeg → Whisper → transcription
```

**AI Bot:**
```
FSEvents detects DB change → SQLCipher query → trigger match → Claude API → AppleScript send
```

---

## Quick Start

### Prerequisites

```bash
brew install sqlcipher ffmpeg
pip install -r requirements.txt

# SILK decoder (for voice transcription)
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make
```

### 1. Extract encryption keys

WeChat databases are encrypted. Extract keys once (re-extract after major updates):

```bash
# Re-sign WeChat to allow debugger
sudo codesign --force --deep --sign - /Applications/WeChat.app

# Extract (WeChat must be running)
lldb -p $(pgrep -x WeChat) \
     -o "script exec(open('scripts/keys/extract_key2.py').read())" \
     -o "quit"
```

### 2. Export chat history

```bash
python3 scripts/export_chat.py --name "nickname"
```

### 3. Transcribe voice messages (optional)

```bash
python3 scripts/transcribe_voices.py --name "nickname"
python3 scripts/export_chat.py --name "nickname" --voice-json name_voice_map.json
```

### 4. Run AI bot (optional)

```bash
# Configure
echo "wxid_xxx" > .watch
echo "sk-ant-xxx" > .apikey

# Launch
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
wechat-ai-bot/
├── scripts/
│   ├── export_chat.py          # Chat history → Markdown
│   ├── transcribe_voices.py    # Voice → Whisper transcription
│   ├── start.py                # Bot launcher
│   ├── bot.py                  # Bot main process
│   ├── config.py               # Configuration (env vars)
│   ├── dashboard.py            # Web dashboard (localhost:7788)
│   ├── keys/
│   │   └── extract_key2.py     # LLDB key extraction
│   └── core/                   # Internal modules (AI, contacts, RAG, etc.)
├── SKILL.md                    # Claude Code skill definition
├── requirements.txt
└── .gitignore
```

---

## Security

- `keys/wechat_keys.json` — gitignored, never commit
- Exported chat files — gitignored
- API keys — environment variables only
- Re-sign WeChat only for key extraction, restore afterward

---

## Limitations

- macOS only (FSEvents + AppleScript)
- WeChat Mac client must be running
- Keys must be re-extracted after major WeChat updates
- Voice transcription requires ~2.8s per message (Whisper `small` model)
