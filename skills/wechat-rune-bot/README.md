# WeChat Rune — Bot

> **v0.1.0** · Updated 2026-04-19 · `wechat · bot · claude` · by [@xjin6](https://github.com/xjin6)

A Claude-powered WeChat auto-reply bot. Monitors selected conversations, drafts replies
in your voice based on your chat history, and sends them via keyboard automation
(Windows) or AppleScript (Mac).

Works on **macOS** and **Windows** from the same codebase. Reuses the same key-extraction
pipeline as `wechat-rune-export`, so if you've already set up that skill, the bot picks
up your existing `scripts/keys/wechat_keys.json` and skips straight to configuration.

## What it does

- Watches a user-specified list of contacts/groups for new messages
- For each incoming message, pulls recent chat history as context
- Asks Claude to draft a reply matching your tone/style
- Sends the reply back through the WeChat UI (no third-party API, no bot platform)
- Optional live dashboard at `localhost:7788` showing intercepted messages + drafted replies

## Quick start

```bash
# From repo root:
# (If keys not extracted yet, this skill walks you through it first — same as wechat-rune-export.)

echo "wxid_abc123" > .watch          # who to monitor (newline-separated)
echo "sk-ant-xxx" > .apikey          # Anthropic API key

python scripts/start.py              # launch bot
python scripts/dashboard.py          # (optional) open http://localhost:7788
```

## Permissions

- **Mac**: System Settings → Privacy & Security → Accessibility → enable Terminal.
  Without this, AppleScript can't type into the WeChat window.
- **Windows**: no extra permissions, but keep the WeChat window open and on top — the
  bot uses keyboard simulation, so it breaks if the window is minimized or hidden.

## Installing this skill

```bash
ln -s /absolute/path/to/wechat-rune/skills/wechat-rune-bot \
      ~/.claude/skills/wechat-rune-bot
```

Ask Claude "set up a WeChat auto-reply bot" to trigger.

## Related

- [`wechat-rune-export`](../wechat-rune-export/) — if you also want a static Markdown
  dump of your chat history (separate from the real-time bot).

## License

MIT
