# wechat-rune

Two Claude Code skills for working with your local WeChat (微信) data on **macOS** and **Windows**:

| Skill | Purpose |
|---|---|
| [`wechat-rune-export`](skills/wechat-rune-export/) | Decrypt + export chat history to Markdown, with Whisper voice transcription and AI homophone correction |
| [`wechat-rune-bot`](skills/wechat-rune-bot/) | Claude-powered auto-reply bot — drafts replies in your voice, sends them through the WeChat UI |

Both skills share one key-extraction pipeline and one decryption core. If you've set up
the export skill, the bot skill reuses the same `scripts/keys/wechat_keys.json` and jumps
straight to bot config.

## Why "rune"

Every WeChat SQLCipher database is sealed behind a 64-character hex key — a string like
`aa713385968bb2d953fdb6b1f79b83f1e25aac99bd9bb78ea7a31219e274322a`. That's a modern rune:
a short cryptic symbol carrying the power to unlock everything written behind it. These
skills find the runes your WeChat process holds in memory and use them to read your own
data back.

## Requirements

| | Mac | Windows |
|---|---|---|
| OS | macOS 13+ (Apple Silicon or Intel) | Windows 10+ |
| Runtime | Python 3.10+, Xcode CLT (for `cc`) | Python 3.10+ |
| WeChat version | 4.x (Weixin) | 4.x (Weixin) |
| Extra deps | `brew install sqlcipher ffmpeg`, `silk-v3-decoder` compiled | `pip install -r requirements.txt` (covers everything) |
| Privileges needed | `sudo` (once for codesign, once per key extraction) | None |

## Install

Clone, then symlink the two skills into Claude Code's skill directory:

```bash
git clone https://github.com/xjin6/wechat-rune.git ~/Desktop/wechat-rune
cd ~/Desktop/wechat-rune
pip install -r requirements.txt

ln -s "$PWD/skills/wechat-rune-export" ~/.claude/skills/wechat-rune-export
ln -s "$PWD/skills/wechat-rune-bot"    ~/.claude/skills/wechat-rune-bot
```

Relaunch Claude Code. Then:
- "Export my WeChat chat history" → triggers `wechat-rune-export`
- "Set up a WeChat auto-reply bot" → triggers `wechat-rune-bot`

## Usage — works for any contact

Nothing is pinned to specific people or machine paths. You name the contacts
(by **wxid + a short label**) and the folder roots on each run, so the same tools
update whoever you point them at.

**Keys.** `scripts/keys/wechat_keys.json` must hold the keys for the WeChat account
whose data you're reading. Keys accumulate in `scripts/keys/wechat_keys_pool.json`
(append-only) and `keystore.py` re-resolves the one working key per database — so a
partial re-scan can never wipe good keys. Capture/refresh with
`scripts/keys/extract_key_windows.py`, or import an external dump with
`python scripts/keys/import_keys.py <file>`.

**One-click incremental update (Windows).** Pass the contact list + roots:

```powershell
.\update_wechat.ps1 -Contacts "wxid_aaaaaaaaaaaa:alice,wxid_bbbbbbbbbbbb:bob" `
    -Rel "<relationship root>" -Wiki "<obsidian wiki root>"
# -KeyFile <dump> imports keys first; -Correct adds AI homophone correction.
```

It runs: incremental diff → transcribe new voices (resume) → re-export each
`<label>_wechat.md` → sync an image-less copy to the Obsidian wiki. Roots can also
come from env vars `WECHAT_VIBE_ROOT` / `WECHAT_WIKI_ROOT` (then `-Rel/-Wiki` are
optional).

**Individual steps** are equally generic:

```bash
python scripts/export_chat.py --wxid wxid_aaaaaaaaaaaa --out alice.md
python scripts/incremental_diff.py "<relationship root>" "wxid_aaaaaaaaaaaa:alice"
python scripts/sync_to_wiki.py alice --vibe-root <…> --wiki-root <…>
```

## Repository layout

```
wechat-rune/
├── skills/
│   ├── wechat-rune-export/
│   │   ├── SKILL.md          # Claude Code skill manifest
│   │   └── README.md
│   └── wechat-rune-bot/
│       ├── SKILL.md
│       └── README.md
├── scripts/                   # shared code used by both skills
│   ├── keys/
│   │   ├── extract_key_macos.c     # Mac: compile once, run with sudo
│   │   ├── extract_key_windows.py  # Windows: Python + ctypes
│   │   ├── keystore.py             # append-only, multi-candidate key store
│   │   └── import_keys.py          # import + validate keys from any external dump
│   ├── export_chat.py
│   ├── incremental_diff.py         # per-contact delta diagnostic
│   ├── sync_to_wiki.py             # image-less sync to an Obsidian vault
│   ├── transcribe_voices.py
│   ├── start.py                    # bot launcher
│   ├── bot.py                      # bot main loop
│   ├── dashboard.py                # live web dashboard
│   ├── config.py
│   └── core/                       # RAG, embeddings, sender, decrypt, contacts
├── update_wechat.ps1               # Windows one-click incremental-update runner
├── requirements.txt
├── README.md                       # this file
└── README_CN.md                    # 中文版
```

## Privacy & legal

- Everything runs locally on your own machine against your own decrypted WeChat data.
- Nothing is uploaded to third-party servers except Anthropic API calls (only when the
  bot is running, and only the relevant message window, not your full history).
- The key extraction mechanism (reading your own process memory) is explicitly allowed
  by macOS/Windows for processes you own. You are not bypassing any server-side security;
  you are reading the keys your own WeChat client holds for itself.
- Don't use the bot to impersonate others in contexts where that's misleading or harmful.

## License

MIT. See [LICENSE](LICENSE).

## Credits

- C key scanner adapted from community reverse-engineering of WeChat 4.x SQLCipher storage
- SILK audio decoder: [kn007/silk-v3-decoder](https://github.com/kn007/silk-v3-decoder)
- Whisper: [openai/whisper](https://github.com/openai/whisper)
