# wechat-ai-bot

A WeChat Mac desktop bot powered by Claude AI. It monitors specified conversations, detects trigger words, and replies automatically — all through local SQLite access and AppleScript, with no unofficial WeChat protocols.

> **macOS only.** Requires WeChat Mac desktop client.

---

## How It Works

```
WeChat writes message → FSEvents detects DB change → SQLCipher queries encrypted DB
→ trigger word match → Claude API generates reply → AppleScript sends it
```

- **Message detection**: FSEvents watches WeChat's SQLite WAL file for near-instant detection
- **DB access**: Queries the encrypted SQLite database directly via SQLCipher (no full decryption)
- **Memory**: Each conversation has its own in-memory deque (configurable history window), no cross-chat contamination
- **Contact identification**: On-demand lookup from `contact.db`, displays both remark and nickname (e.g. `Alice(chain)`)
- **Sending**: AppleScript controls the WeChat Mac input field

---

## Prerequisites

```bash
# SQLCipher for reading the encrypted WeChat DB
brew install sqlcipher

# Python dependencies
python3 -m pip install anthropic watchdog zstandard
```

**Permissions required:**
- macOS Accessibility access for Terminal (System Settings → Privacy & Security → Accessibility)

---

## Setup

### 1. Extract the database encryption key

WeChat's local SQLite databases are encrypted. You need to extract the key once.

**Step 1** — Re-sign WeChat to allow LLDB attachment:
```bash
sudo codesign --force --deep --sign - /Applications/WeChat.app
```

**Step 2** — With WeChat running and logged in, run:
```bash
cd keys
lldb -p $(pgrep -x WeChat) -o "script exec(open('extract_key2.py').read())" -o "quit"
```

Keys are saved to `keys/wechat_keys.json`.

**Step 3** — Restore WeChat's original signature by reinstalling from the App Store.

> The key stays valid across restarts and minor WeChat updates. Re-extract after major version updates or account changes.

### 2. Find your configuration values

**Your wxid**: Look at the path of the extracted key file — the folder name before `_c092` is your wxid.

**Conversation IDs**: Open WeChat, go to the chat you want to monitor. The chat's internal ID can be found in the database. Group IDs end with `@chatroom`, personal chats use `wxid_xxxxxxxxxx`.

**Database path**: After extracting the key, the path shown in `wechat_keys.json` contains your wxid and the path suffix.

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your actual values
source .env
```

### 4. Run

```bash
ANTHROPIC_API_KEY=sk-ant-... python3 bot.py
```

---

## Trigger Rules

| Sender | Trigger |
|--------|---------|
| Yourself | Message contains `/xin` (or your configured self-trigger) |
| Others | Message contains any word in `BOT_TRIGGERS`, or @-mentions you |
| Bot replies | Never contains `/xin` (prevents self-triggering loop) |

---

## Configuration Reference

All settings are via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Your Anthropic API key |
| `WECHAT_MY_WXID` | — | **Required.** Your WeChat internal user ID |
| `WECHAT_WATCH_IDS` | — | **Required.** Comma-separated list of chat IDs to monitor |
| `WECHAT_DB_PATH` | auto | Path to WeChat's `message_0.db` |
| `BOT_TRIGGERS` | `小昕,/xin` | Trigger words (comma-separated) |
| `AI_MODEL` | `claude-haiku-4-5-20251001` | Claude model to use |
| `AI_MAX_TOKENS` | `1500` | Max tokens per reply |
| `REPLY_PREFIX` | `👾 ` | Prefix added to every bot reply |
| `MAX_HISTORY` | `50` | Number of messages kept per conversation |

---

## Project Structure

```
wechat-ai-bot/
├── bot.py              # Main entry point
├── config.py           # Configuration (all via env vars)
├── core/
│   ├── ai.py           # Claude API calls + history building
│   ├── contacts.py     # On-demand contact name lookup
│   ├── decrypt.py      # SQLCipher query wrapper
│   ├── reader.py       # Message parsing and decoding
│   └── sender.py       # AppleScript send + markdown stripping
└── keys/
    ├── extract_key2.py     # Key extraction script (run inside LLDB)
    ├── extract_key.lldb    # LLDB automation script
    └── wechat_keys.json    # Your extracted keys (gitignored, never commit)
```

---

## Security Notes

- `keys/wechat_keys.json` is gitignored — never commit it
- The decrypted DB cache (`db/`) is also gitignored
- API keys are read from environment variables only
- Re-signing WeChat is needed only once for key extraction, then restore the original signature

---

## Limitations

- macOS only (uses FSEvents + AppleScript)
- WeChat Mac desktop client must be running
- Bot replies go to whichever WeChat chat window is currently open
- History window is limited to the last N messages (configurable)
- Key must be re-extracted after major WeChat version updates
