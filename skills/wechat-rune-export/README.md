# WeChat Rune — Export

> **v0.1.0** · Updated 2026-04-19 · `wechat · export · whisper` · by [@xjin6](https://github.com/xjin6)

Extracts WeChat chat history out of the local encrypted SQLCipher databases into clean
per-conversation Markdown files, with optional Whisper-powered voice-message transcription
and AI homophone correction.

Works on **macOS** and **Windows** from the same codebase.

## What it does

1. Walks the user through syncing their phone → desktop WeChat (same-Wi-Fi QR)
2. Extracts SQLCipher encryption keys from the running WeChat process
   - Mac: `task_for_pid` via compiled C binary + `sudo`
   - Windows: `ReadProcessMemory` via Python + ctypes (no Admin needed)
3. Decrypts message/contact DBs and renders a per-conversation Markdown file
4. (Optional) Transcribes all voice messages via OpenAI Whisper (`small` by default, `medium` for higher accuracy)
5. (Optional) Lets Claude batch-correct obvious homophone errors in the transcripts

## Quick start

```bash
# From repo root:
# Mac first-time setup
sudo codesign --force --deep --sign - /Applications/WeChat.app
cc -O2 -o scripts/keys/extract_key_macos scripts/keys/extract_key_macos.c -framework Foundation
sudo ./scripts/keys/extract_key_macos

# Windows first-time setup
python scripts/keys/extract_key_windows.py

# Then, on either platform:
python scripts/export_chat.py --name "nickname"
python scripts/transcribe_voices.py --name "nickname"    # optional
```

## Output

A file named `<nickname>_聊天记录.md` in the repo root with every message in chronological
order, sender names resolved, voice messages transcribed inline. Supported message types:
text, image, voice (with transcript), video, sticker, file, transfer, red packet, link,
call log, official account forward, mini program card, quote reply, system events.

## Installing this skill

Symlink the skill folder into your Claude Code skills directory:

```bash
ln -s /absolute/path/to/wechat-rune/skills/wechat-rune-export \
      ~/.claude/skills/wechat-rune-export
```

Claude Code will pick it up on next launch. Then just ask Claude to "export my WeChat
chat history" and it'll walk you through the flow.

## Related

- [`wechat-rune-bot`](../wechat-rune-bot/) — optional follow-on: let Claude auto-reply
  to your WeChat messages in your voice, reusing the same decryption pipeline.

## License

MIT
