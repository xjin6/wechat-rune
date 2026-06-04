---
name: wechat-rune-export
description: >
  Export WeChat (微信/Weixin) chat history to Markdown on macOS or Windows,
  including voice message transcription (语音转文字) via Whisper, AI
  homophone correction, image decryption (Weixin 4.x V2 .dat),
  inline-thumbnail embedding, and per-image AI Chinese descriptions.
  Walks the user through syncing from phone, extracting SQLCipher
  encryption keys from process memory, decrypting databases AND attached
  images, and producing a self-contained per-conversation Markdown file
  where text + voice transcripts + image thumbnails + image descriptions
  all live inline. Trigger this skill whenever the user wants to: export
  WeChat chat records, decrypt the WeChat database, back up their WeChat
  history, extract WeChat encryption keys, transcribe WeChat voice
  messages, correct voice transcriptions, decrypt WeChat .dat image
  files, embed WeChat images in markdown, generate AI descriptions for
  chat images, or asks anything about reading local WeChat data on Mac
  or Windows.
---

# WeChat Chat History Export

Works on **macOS** and **Windows** from the same codebase. Four steps.
**Detect platform automatically. Guide interactively — wait for user confirmation at each step.**

> **Workspace convention.** All file paths and shell commands below are relative to the
> repo root (default `~/Desktop/vibe-coding/wechat-rune`). Before running any command,
> `cd` there first.

| Step | Description |
|------|-------------|
| 1 | Sync phone chat history to computer |
| 2 | Extract database encryption keys |
| 3 | Export chat + voice transcription + AI correction |
| 4 | (Optional) Decrypt images + AI describe + re-export with thumbnails inline |

---

## Step 1: Sync Phone Chat History

Ask the user: "Have you already synced messages from your phone, or do you need to do that first?"

**How to sync:**
1. Log in to WeChat / Weixin on the computer
2. Phone → WeChat → Settings → General → Chat History Migration & Backup → Migrate to Desktop
3. Select chats, same Wi-Fi, scan QR code

> ⚠️ **Important:** After the phone sync completes, new messages may have created a new
> database shard (`message_1.db`, `message_2.db`, etc.). Each new database has its own
> encryption key. You **must** restart WeChat/Weixin and immediately run key extraction
> (Step 2) before the keys leave memory. Do not skip this even if you extracted keys before.

---

## Step 2: Extract Database Encryption Keys

WeChat databases are encrypted with SQLCipher. Keys live in process memory only briefly
after Weixin opens its databases. **Extract immediately after launch.**

**First: check whether the key file already exists and is still valid.**

```bash
python -c "
import json, os
p = 'scripts/keys/wechat_keys.json'
if not os.path.exists(p):
    print('MISSING'); exit()
d = json.load(open(p))
print(f'EXISTS: {len(d)} keys')
"
```

If it prints `EXISTS: N keys` (N > 0) and the user hasn't just done a phone sync, skip the
rest of Step 2 and jump to Step 3. Otherwise, run the platform-specific extraction below.

### Windows

Run directly — Administrator may be needed for some memory regions:

```bash
python scripts/keys/extract_key_windows.py
```

The script auto-targets the **main** `Weixin.exe` process (no `--type=` cmdline flag).
Newer Weixin (4.x) is multi-process (Chromium-style); only the parent holds the SQLCipher
heap. Subprocesses (wxocr, wxplayer, wxpublic, wxutility) won't yield keys.

**If it returns "No keys found":**
1. Completely close Weixin (check tray icon + Task Manager for any `Weixin.exe`)
2. Reopen Weixin and **click into a chat + Contacts tab** so the DBs actually get opened
3. Run the script again within ~30 seconds
4. If still empty, re-run as **Administrator** (right-click → Run as Admin); on some
   builds, kernel-protected memory regions are only readable with elevation.

> Expected: 15–20 keys. If you recently synced from phone and a new `message_N.db`
> was created, that database's key will only be captured on the next Weixin restart.

### Mac

```bash
# Install dependencies (first time only)
brew install sqlcipher ffmpeg
pip install -r requirements.txt
git clone https://github.com/kn007/silk-v3-decoder.git /tmp/silk-v3-decoder
cd /tmp/silk-v3-decoder/silk && make

# Re-sign WeChat (one-time; must be fully quit first, then reopened after).
# Hardened runtime on the Tencent-signed binary blocks task_for_pid until
# this is done.
sudo codesign --force --deep --sign - /Applications/WeChat.app

# Build the key extractor (one-time)
cc -O2 -o scripts/keys/extract_key_macos \
    scripts/keys/extract_key_macos.c -framework Foundation

# Extract (WeChat must be running, with at least one chat opened so its
# DBs are loaded). Root is required because task_for_pid at root reaches
# the SQLCipher heap regions that hold the x'KEY+SALT' pattern; an
# ordinary-user LLDB attach cannot see those regions on WeChat 4.x.
sudo ./scripts/keys/extract_key_macos
```

> Expected: ~18 keys (one per active DB shard). The output file
> `scripts/keys/wechat_keys.json` is written alongside the binary in flat
> format, identical to what the Windows extractor produces — so every
> downstream script is cross-platform from here on.

> **Don't wrap in `osascript ... with administrator privileges`.** macOS
> sandboxes processes launched via `AuthorizationExecuteWithPrivileges`,
> and `task_for_pid` is blocked in that context. A direct terminal `sudo`
> is the only invocation that works.

---

## Step 3: Export Chat History

This step is interactive. Follow this flow:

### 3a. Basic export first

Ask: "Whose chat history would you like to export? (nickname, remark name, or group name)"

```bash
python scripts/export_chat.py --name "nickname"             # private chat
python scripts/export_chat.py --name "group name" --group   # group chat
```

Show the user the result — number of messages and file size. Let them see the Markdown file.
This gives them immediate confirmation that it's working before the longer voice step.

### 3b. Voice transcription (ask user)

After the basic export, ask: "There are N voice messages. Want to transcribe them?
It takes ~3–4 seconds per message (Whisper `small` model), so roughly X minutes total.
Use `--model medium` for higher accuracy but ~2× slower."

```bash
python scripts/transcribe_voices.py --name "nickname"
# or for higher accuracy:
python scripts/transcribe_voices.py --name "nickname" --model medium
```

Run this as a background task. **Report progress to the user periodically** — show them
sample transcribed lines every 50 entries so they know it's working.

### 3c. AI correction (do this automatically — no user input needed)

After transcription completes, immediately correct the voice_map.json by **reading the
actual content in batches of 50 entries**. Do NOT rely on a pre-defined error list —
that approach only catches patterns seen before and misses anything new.

For each batch:
1. Read the transcribed text as natural language
2. Ask: does each sentence make semantic sense in Chinese?
3. Fix words that are phonetically plausible mishears (same sound, wrong character)
4. Common categories to watch for — but always judge from context, not from this list:
   - Proper nouns: school names, app names, product names, company names
   - Technical terms: 用研 (UXR), 选修课, 劳动课, specific tools/platforms
   - Audio noise at end of long recordings (repeated characters like GGGG…)

Write corrections back with `"corrections": ["wrong→right"]` field. Then re-export:

```bash
python scripts/export_chat.py --name "nickname" --voice-json nickname_voice_map.json
```

---

## Step 4: (Optional) Decrypt + Embed Images

Ask: "Want to also decrypt the image attachments and embed thumbnails (with AI Chinese
descriptions) inline in the markdown? It takes ~30 min for ~400 unique images."

If yes, run the full image pipeline below. Each sub-step has a quick smoke test before
the heavy work, so failures are caught early.

### 4a. Locate the contact's image folder

Each conversation's images live under
`<xwechat_files>/<account>/msg/attach/<MD5(contact_wxid)>/<YYYY-MM>/Img/*.dat`.
Compute the MD5 of the contact's wxid (the **real** wxid, e.g. `wxid_clslfiswnis422`,
not the user-set 微信号), then point the next scripts at that folder.

```bash
python -c "
import hashlib
print(hashlib.md5(b'wxid_clslfiswnis422').hexdigest())
"
```

### 4b. Derive the image AES key

WeChat 4.x wraps images in a V2 container (`07 08 V2 08 07` magic) holding an
AES-128-ECB ciphertext + raw bytes + 1-byte XOR. The AES key is **per-account, not
per-file**, and is `md5(str(uin) + wxid)[:16]` (ASCII).

`find_image_key.py` derives `uin` by brute force using two constraints we can compute
from disk:
- `xor_key = uin & 0xFF` — voted from a few V2 .dat files (their last byte XOR 0xD9
  always equals the xor_key because every JPEG ends with `FF D9`)
- `md5(str(uin)).hexdigest()[:4]` equals the 4-hex suffix on the account folder
  (e.g. `magicxinjx_c092` → suffix `c092`)

```bash
python scripts/find_image_key.py \
  --attach-dir "<full path to MD5 folder>" \
  --account-folder "magicxinjx_c092"
```

Expected output: `FOUND: uin=… aes_key=… xor_key=…` in <10 seconds. Save the
hex-string `aes_key` for the next step.

> If brute force fails: confirm the account folder name actually ends in 4 hex chars
> (e.g. `_c092`). If the folder name is something else (long auto-wxid like
> `wxid_iv139ys0vn3412_4ae6`), the suffix `4ae6` is still 4 hex chars — use that.

### 4c. Bulk-decrypt all .dat files

```bash
python scripts/decrypt_images.py \
  --attach-dir "<full path to MD5 folder>" \
  --out-dir "<output>/images" \
  --aes-key "<aes_key from 4b>"
```

This runs locally in 1–2 min for ~1500 files. Produces JPG/PNG/HEVC files preserving
the `YYYY-MM/Img/` folder layout. Three filename suffixes appear:
- `<id>_t.<ext>` — thumbnail (small JPEG preview, always present)
- `<id>_h.<ext>` — HD original (sometimes)
- `<id>.<ext>` — original (sometimes)

> The `--probe` flag decrypts only one file as a smoke test — use this first to verify
> the key before committing to bulk decryption.

### 4d. Build the file index

This maps `md5(decrypted-content)` → `{thumb, hd, orig, animated}` so the export
script can look up files by the md5 attribute in each message's XML.

```bash
python scripts/build_image_index.py "<output>/images" "<output>/image_index.json"
```

Expected: ~600 entries for a typical relationship. Watch coverage when you re-export
— typically ~70% of `[图片]` messages will map to a file (older messages get auto-cleaned
by Weixin and have no local copy).

### 4e. Generate AI descriptions

First, generate the to-describe list — this is filtered to **non-animated** images only
(real photos/screenshots; animated wxgf stickers get just a thumbnail, no description,
since the thumb conveys the joke):

```bash
python scripts/build_describe_list.py "<contact_wxid>" \
  "<output>/image_index.json" "<output>/describe_list.json"
```

Then split into 8 batches and **delegate to 8 parallel `general-purpose` agents**.
This is dramatically faster + cheaper than describing 400 images sequentially in the
main context.

Each agent gets:
- Input: a per-batch JSON of `[{md5, path}, ...]`
- Task: Read each thumbnail, write a 20–50-char Chinese description focused on
  content type + key visible elements + readable text snippets
- Output: write a JSON `{md5: description, ...}` to a known per-batch path

> **Heads-up: agents sometimes emit invalid JSON** when descriptions contain unescaped
> ASCII double quotes (Chinese text often references English words/labels with `"..."`).
> After agents finish, verify each batch JSON parses; if any fail, recover by reading
> the file as text and re-parsing line-by-line with a tolerant regex
> `r'"([a-f0-9]{32})":\s*"(.+)'` (strip trailing `"` and `,`).

Merge the 8 batch outputs into one `image_descriptions.json` keyed by content md5.

### 4f. Re-export with images and descriptions

```bash
python scripts/export_chat.py --name "nickname" \
  --voice-json "nickname_voice_map.json" \
  --image-index "<output>/image_index.json" \
  --image-descriptions "<output>/image_descriptions.json"
```

`[图片]` messages now render inline as:

```
[图片: <AI Chinese description>] ![](images/2026-03/Img/<id>_t.jpg)
```

The description is inside the same bracket as the type tag so it does NOT collide
with markdown's `>` blockquote (which is also used by quote-reply rendering).
Animated stickers render as `[动图] ![](images/...)` (no description — the thumb
conveys the joke). If the recipient of the .md doesn't have the `images/` folder,
the inline thumbnails fail to load silently but the description still conveys the
content.

> **Image-description prompt — write transcription-first.** When generating
> descriptions, agents must prefer FAITHFUL TEXT TRANSCRIPTION over generic visual
> summary. For chat screenshots / documents / social posts, quote the visible
> Chinese text VERBATIM using Chinese 「」 / 『』 quotes (never ASCII `"` — it
> breaks the JSON file). For photos, 30–80 chars on subject + setting. Never
> emit `无法识别` if any text is visible.

### 图片 vs 动图 classification

We don't decode the wxgf container to count frames. Instead we use the message XML's
`originsourcemd5` attribute:
- **non-empty** → user-uploaded content (photo / screenshot, even if encoded as wxgf
  HEVC) → `[图片]`
- **empty** → forwarded sticker / animation that lost its source provenance → `[动图]`

This is **not 100% accurate** — a few static images forwarded multiple times will get
tagged `[动图]`. But it's correct ~95% of the time and trivially fast.

### Supported Message Types

| Type | Output |
|------|--------|
| Text | plain text |
| Image (with index + description) | `[图片: …描述…] ![](images/.../id_t.jpg)` (inline) |
| Image (fallback, no index) | `[图片]` |
| Animated sticker | `[动图] ![](images/.../id_t.jpg)` (no description) |
| Voice | `[语音 9s] transcribed text <!-- correction: X→Y -->` |
| Video | `[视频 23s]` |
| Sticker | `[表情包]` |
| Official Account | `[公众号 \| Name] title` |
| Mini Program | `[小程序 \| Name] title` |
| File | `[文件] filename` |
| Transfer (mine — sent or any state) | `[转账 ¥X]` (subject = always just the amount) |
| Transfer (contact accepted, pst=3/8) | `[已收钱 ¥X]` |
| Transfer (contact returned, pst=4 paired w/ pst=9) | `[已退回 ¥X]` |
| Transfer (contact-incoming pending, pst=4) | `[请收钱 ¥X]` |
| Transfer (contact rejected, pst=5) | `[已被拒收 ¥X]` |
| Red Packet | `[红包 note]` |
| Link | `[链接] title` |
| Call | `[语音通话 120s]` / `[视频通话 未接通]` |
| Quote Reply | text + `> quoted` (nested, with sender + time) |
| System | *italic* |

### Transfer (转账) rendering rule — subject vs object

A WeChat transfer event produces multiple DB rows with the same `transcationid`
(initiation + state updates). We render **each row independently** (no dedup) but
choose the label based on **who authored the row** (the message's `real_sender_id`
mapped via `Name2Id`):

- **My rows (is_me = True)**: always `[转账 ¥X]` regardless of `paysubtype`.
  The user is the subject of the chat export — system status updates like "已被领取" /
  "已退还" on my side are conveyed by the *contact's* paired row instead.
- **Contact's rows (is_me = False)**: label per `paysubtype`:
  - `3` / `8` → `已收钱`
  - `4` → `请收钱`, BUT if the same `transcationid` has a paired pst=9 row (my-side
    refund notification), relabel to `已退回`. This is what makes refunds explicit
    on the contact's side. The refund-tid set is built by a pre-scan in
    `_build_refund_tids()` before the main loop.
  - `5` → `已被拒收`
  - else → `转账`

`<des>` is NOT perspective-aware (always "收到转账X元..." legacy fallback) — do NOT
use it as the rendered text. `<feedesc>` is authoritative for the amount.

---

## Step 5: Incremental Updates (for subsequent runs)

After the initial full export, subsequent updates should be incremental — only do
the work that's needed for new content. A typical "add the last week" update
finishes in 5–10 minutes instead of 1+ hour.

### 5a. Before re-extracting keys: open every chat

Critical: **open Weixin and click into every contact you'll update before key
extraction**. Each `message_N.db` shard only has its PRAGMA key in memory after
WeChat has opened a chat that lives in that shard. Skip this and the affected
shard's contact data appears to vanish from the DB (sqlite_master shows 0
`Msg_*` tables for that shard). See Troubleshooting.

After the user confirms they've clicked through:

```bash
python scripts/keys/extract_key_windows.py
```

### 5b. Diff diagnostic

Compute per-contact deltas in one shot:

```bash
python scripts/incremental_diff.py "<relationship_root>" \
  "wxid_<contact1>:<label1>" "wxid_<contact2>:<label2>"
```

Output tells you exactly what work is needed — 0 voices new for one contact,
35 image MD5s new for another, etc. Skip dimensions where the delta is 0.

### 5c. Voice transcription (resume mode)

`transcribe_voices.py` natively supports resume — pass the same `--out` path and
it loads the existing voice_map, skips entries already present, and only
transcribes net-new timestamps:

```bash
python scripts/transcribe_voices.py --name "nickname" --model medium \
  --out path/to/existing/voice_map.json
```

Stale entries (voice_map keys not in current DB after a phone re-import) stay
in the file — harmless, the export script just doesn't reference them.

### 5d. AI-correct only the net-new voices

Don't re-process already-corrected entries. Identify net-new by diffing
against the prior `voice_batches/vbatch_*.json` files (their keys are the
previous voice_map state):

```python
prior_keys = set()
for p in sorted(glob.glob("voice_batches/vbatch_*.json")):
    prior_keys.update(json.load(open(p)).keys())
new_entries = {k: v for k, v in voice_map.items() if k not in prior_keys}
# Stage to /tmp/<contact>_voice_new.json → run correction agent on it
```

Then dispatch a workflow: **correct → adversarially verify**. The verifier
catches false positives (e.g. forced "用研" where context was actually about
literal "eye use"). Don't skip the verify pass — Whisper-medium homophone
correction has ~5–15% false-positive rate without it.

### 5e. Images: decrypt, rebuild, diff descriptions

`decrypt_images.py` is idempotent — re-running over the same out-dir processes
new `.dat` files only:

```bash
python scripts/decrypt_images.py --attach-dir "<contact_md5_folder>" \
  --out-dir "<contact>/images" --aes-key "<aes_key>"
```

Then rebuild index + describe_list, then diff describe_list md5s against
existing `image_descriptions.json` keys:

```python
described = json.load(open("image_descriptions.json"))
desc_list = json.load(open("describe_list.json"))
new = [d for d in desc_list if d["content_md5"] not in described]
```

For small deltas (< 20 images), one agent handles it. Larger → split into
4–8 batches and use the standard parallel-agents prompt from Step 4e.

### 5f. Re-export

```bash
python scripts/export_chat.py --name "nickname" --out <output.md> \
  --voice-json voice_map.json --image-index image_index.json \
  --image-descriptions image_descriptions.json
```

The export is fast (5–30 sec) and uses whichever JSON files you've updated.
Final message counts may not equal "old + new" — phone re-imports can cause
message renumbering or trim old archived messages. That's expected; don't try
to reconcile.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No keys found" on Windows | Close Weixin fully → reopen → click into a chat + Contacts tab → run script within 30s |
| Key extractor finds 0 keys but Weixin is running | Likely targeting a subprocess. Verify the script picks the **main** PID (no `--type=` cmdline). On newer Weixin 4.x the auto-detect handles this; if still failing, run as Administrator. |
| "No keys found" after phone sync | New database created — must restart Weixin to capture new key |
| `task_for_pid failed` (Mac) | WeChat not ad-hoc signed — rerun `sudo codesign --force --deep --sign - /Applications/WeChat.app` with WeChat **fully quit** first |
| `extract_key_macos` returns 0 keys (Mac) | Forgot `sudo`, or WeChat hasn't opened a chat yet — click a conversation, then rerun |
| Empty export | Re-extract keys; check wxid spelling |
| `sqlcipher3` errors | `pip install sqlcipher3` |
| Voice in Traditional Chinese | Change `initial_prompt` in `scripts/transcribe_voices.py` |
| No key for `media_0.db` | Re-extract immediately after Weixin restarts |
| `find_image_key.py` brute force fails | Confirm `--account-folder` value ends in 4 hex chars; that suffix is the search constraint |
| Decrypt probe "no recognized format magic" | Wrong AES key OR wrong attach folder. Recheck the MD5 of the contact's wxid and the brute-force output. |
| Image descriptions JSON won't parse | An agent emitted unescaped quotes — recover with line-by-line regex parsing (see Step 4e) |
| `[图片]` not embedding despite description being present | Description JSON must be keyed by md5(decrypted-content), **not** by the filename basename. Filenames use a different hash. The XML's md5 attribute equals md5(decrypted ORIGINAL file) — NOT md5(HD). `build_image_index.py` indexes all three variants (thumb/hd/orig) under their respective md5s, so any of them matches. |
| Many `[图片]` messages with no embed | Coverage is naturally ~70% — Weixin auto-cleans old originals from disk for older chats, leaving thumb-only groups whose thumb md5 ≠ XML md5. Nothing to do client-side; the messages remain as bare `[图片]`. |
| Transfer rendering shows "已退还" on my side instead of "已退回" on contact's side | Old behavior. The new rule: my (subject) rows always show plain `[转账 ¥X]`; contact's (object) row shows the state. For refunds, pst=4 contact-row is relabeled `已退回` when paired with a my-side pst=9. |
| `incremental_diff.py` shows `⚠ shard message_N.db: contact's Msg_ table not present` | Weixin hadn't opened a chat in that shard when you ran key extraction, so its PRAGMA key isn't in memory and the encrypted DB can't be read. Open Weixin → click into the affected contact's chat → scroll a few messages → re-run `extract_key_windows.py` → re-run the diff. The shard's contact data will reappear. |
| Re-extract returns fewer keys than before | Same root cause as above. Weixin only loads a shard's key when its chat opens. After phone-import + restart, click every contact you'll update before extracting. |
| `transcribe_voices.py` reports `433/93 succeeded, 0s` (instant exit) | Resume mode found N existing entries and re-scanned the DB in a transient state (some shards missing). The script saw fewer voice messages than the voice_map already had, so nothing to do. Click into the relevant chats, re-extract keys, then re-run — it'll find the real new entries. |
| voice_map has "stale" entries (in map but not in DB) after phone re-import | Harmless. Phone re-imports occasionally renumber/replace messages, so old transcribed ts no longer map to any current message. The export script just doesn't reference them. Leave them in; they're cheap to keep and you might want them back. |

---

## Technical Reference

### Key Extraction Timing

Keys appear in memory as `x'<64 hex chars>'` SQL strings only when Weixin executes
`PRAGMA key` on database open. This happens once per database per session. Scan window
is roughly 30–60 seconds after Weixin starts. After phone sync, new database shards
(`message_1.db`, etc.) require a fresh Weixin restart to capture their keys.

### Database Structure

| Database | Contents |
|----------|----------|
| `message_N.db` | Chat messages — table `Msg_` + MD5(wxid). New shards created when current hits ~30MB |
| `contact.db` | Contacts (nick_name, remark, wxid) |
| `media_0.db` | Voice data — `VoiceInfo` table, SILK BLOBs |
| `hardlink.db` | `image_hardlink_info_v4` — content-md5 → on-disk filename mapping (not actually used by the pipeline; we md5 decrypted files directly) |

### Voice Pipeline

| Stage | Mac | Windows |
|-------|-----|---------|
| SILK decode | `silk-v3-decoder` compiled binary | `pilk` pip package |
| Audio conversion | ffmpeg → WAV file | numpy resample → float32 array (no ffmpeg needed) |
| Whisper input | file path | numpy array |

### Image V2 Container Format

```
[6B magic "07 08 V2 08 07"]
[4B aes_size LE]
[4B xor_size LE]
[1B padding]                           = 15-byte header total
[aligned_aes_size bytes AES-128-ECB]   (PKCS7-padded to 16B blocks)
[raw bytes (plaintext)]                (variable length)
[xor_size bytes (single-byte XOR)]
```

`aligned_aes_size = aes_size + (16 - aes_size % 16)` — always at least one extra block.
Concatenating the three decrypted segments gives back the original JPEG/PNG/wxgf bytes.

### AES Image-Key Derivation (cross-platform)

```
xor_key = uin & 0xFF
aes_key = md5(str(uin) + wxid).hexdigest()[:16].encode("ascii")
```

Where `uin` is the account UIN (large integer) and `wxid` is the user's wxid in its
normalized form. We never read process memory for the image key — it's recoverable
entirely from disk via brute force, since the search space (2²⁴ candidates filtered
by an md5 prefix) is small. See `scripts/find_image_key.py`.

### Script Index

| Script | Purpose |
|--------|---------|
| `scripts/keys/extract_key_windows.py` | Windows: extract SQLCipher DB keys (auto-targets main Weixin.exe) |
| `scripts/keys/extract_key_macos.c` | Mac: extract DB keys (compile once, run with sudo) |
| `scripts/export_chat.py` | Export chat to Markdown (with optional `--voice-json`, `--image-index`, `--image-descriptions`) |
| `scripts/transcribe_voices.py` | Batch voice transcription |
| `scripts/find_image_key.py` | Brute-force the V2 image AES key from disk |
| `scripts/decrypt_images.py` | Bulk decrypt V2 `.dat` images to JPG/PNG/HEVC |
| `scripts/build_image_index.py` | Build content-md5 → file-path index after bulk decrypt |
| `scripts/build_describe_list.py` | Filter `[图片]` (non-animated) image messages into a flat list for AI description |
| `scripts/incremental_diff.py` | Per-contact delta diagnostic for Step 5 (incremental updates) — reports new voices / images / .dat counts vs existing artifacts |
