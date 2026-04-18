---
name: wechat-rune-bot
description: >
  Set up a Claude-powered WeChat (微信/Weixin) auto-reply bot on macOS or
  Windows. Monitors selected conversations, drafts replies in the user's
  voice based on their chat history, and sends them via keyboard
  automation (Windows) or AppleScript (Mac). Requires WeChat databases
  to be decryptable first (same key-extraction step as wechat-rune-export);
  this skill performs it automatically if not already done. Trigger when
  the user wants to: set up a WeChat auto-reply bot, have Claude respond
  to WeChat messages for them, configure a WeChat AI assistant, run a
  WeChat reply daemon, or monitor/auto-respond to specific chats.
---

# WeChat AI Auto-Reply Bot

Works on **macOS** and **Windows** from the same codebase. Four steps.
**Detect platform automatically. Guide interactively — wait for user confirmation at each step.**

> **Workspace convention.** All file paths and shell commands below are relative to the
> repo root (default `~/Desktop/vibe-coding/wechat-rune`). Before running any command,
> `cd` there first.

| Step | Description |
|------|-------------|
| 0 | Prerequisite — ensure WeChat DBs are decryptable |
| 1 | Pick conversations to monitor + Anthropic API key |
| 2 | Platform-specific permissions (Mac) or prep (Windows) |
| 3 | Launch the bot |

---

## Step 0: Prerequisite — Ensure Keys Exist

The bot reads historical messages from the same encrypted WeChat DBs that the export
skill works with. If the keys file hasn't been produced yet, walk the user through it
first — without asking anything about exporting chats.

**Check whether keys are already extracted:**

```bash
python -c "
import json, os
p = 'scripts/keys/wechat_keys.json'
if not os.path.exists(p):
    print('MISSING')
else:
    d = json.load(open(p))
    print(f'EXISTS: {len(d)} keys' if d else 'EMPTY')
"
```

- `EXISTS: N keys` (N > 0) → keys present, jump to Step 1 below.
- `MISSING` or `EMPTY` → run platform-specific extraction below, then continue to Step 1.

### Mac key extraction

```bash
# One-time: install deps + ad-hoc re-sign WeChat (WeChat must be fully quit first)
brew install sqlcipher ffmpeg
pip install -r requirements.txt
sudo codesign --force --deep --sign - /Applications/WeChat.app

# One-time: build the extractor
cc -O2 -o scripts/keys/extract_key_macos \
    scripts/keys/extract_key_macos.c -framework Foundation

# Extract (WeChat must be running, with at least one chat opened)
sudo ./scripts/keys/extract_key_macos
```

### Windows key extraction

```bash
pip install -r requirements.txt
python scripts/keys/extract_key_windows.py
```

See the **wechat-rune-export** skill's Troubleshooting section for edge cases
(`task_for_pid failed`, "No keys found", hardened-runtime reset after WeChat updates, etc.).

---

## Step 1: Configure Monitored Conversations + API Key

### 1a. Pick who to watch

Ask: "Which conversations should the bot monitor? Give me nicknames, remark names,
or group names — I'll look up wxids for you."

For each target, look up the wxid from `contact.db` (same mechanism as export_chat.py's
contact search). Write newline-separated wxids to `.watch`:

```bash
echo "wxid_abc123" >  .watch
echo "12345@chatroom" >> .watch
```

### 1b. Anthropic API key

Ask: "What's your Anthropic API key? (`sk-ant-...` — I'll save it to `.apikey` locally;
both `.watch` and `.apikey` are already in `.gitignore`.)"

```bash
echo "sk-ant-xxx" > .apikey
```

### 1c. (Optional) Trigger words / reply style

Defaults in `scripts/config.py`. For custom triggers (e.g. only reply when message
contains `@bot`), override in `.env`:

```bash
cp .env.example .env
# edit .env, set TRIGGER_KEYWORDS, REPLY_STYLE, etc.
```

---

## Step 2: Platform Permissions

### Mac

The bot sends replies via AppleScript automation of the WeChat window. macOS blocks
this until granted:

**System Settings → Privacy & Security → Accessibility → enable Terminal** (or whatever
shell you launched the bot from). After toggling, fully quit and reopen the terminal.

### Windows

No permission setup needed. But: the WeChat window must remain **open and visible** —
the bot uses keyboard-simulation to type into it, so minimizing/hiding it breaks the send.

---

## Step 3: Launch

```bash
python scripts/start.py
# or target a specific watched contact/group:
python scripts/start.py "group name" "contact name"
```

Optional web dashboard (live view of intercepted + drafted messages):

```bash
python scripts/dashboard.py
# → http://localhost:7788
```

Stop the bot with `Ctrl+C` in the terminal where `start.py` is running.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot says "no keys" on startup | Re-run Step 0 — keys file missing or stale |
| AppleScript can't send (Mac) | Accessibility permission for Terminal — see Step 2 |
| Bot can't send (Windows) | WeChat window must be open and focused; don't minimize |
| "No such contact" for watched wxid | Refresh contacts: delete `.watch`, redo Step 1a |
| Bot replies are in the wrong style | Check `.env` for `REPLY_STYLE`; or let bot read more history (needs larger `HISTORY_WINDOW`) |
| Dashboard shows no activity | Make sure `start.py` is running in another terminal |

---

## Script Index

| Script | Purpose |
|--------|---------|
| `scripts/start.py` | Bot launcher — reads `.watch` and `.apikey`, starts `bot.py` |
| `scripts/bot.py` | Main loop — polls DBs, drafts replies via Claude, sends |
| `scripts/dashboard.py` | Web dashboard at localhost:7788 |
| `scripts/config.py` | Defaults for triggers, reply style, history window |
| `scripts/core/` | RAG, embeddings, contact resolution, sender (Mac/Win) |
