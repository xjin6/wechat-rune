#!/usr/bin/env python3
"""
WeChat AI Bot launcher (Windows)

Usage:
    python start.py                    # Start with conversation list from .watch
    python start.py "SSCI Team" John   # Specify conversation names on the fly
"""

import os, sys, json, hashlib, sqlite3

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# ── Auto-detect WeChat account ────────────────────────────────────

def detect_wxid_and_db() -> tuple[str, str]:
    """Return (wxid, msg0_db_path) for the first logged-in WeChat account."""
    base = os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files")
    if not os.path.isdir(base):
        print(f"[!] WeChat Files not found: {base}")
        print("    Please install and log in to WeChat PC first.")
        sys.exit(1)

    for entry in sorted(os.listdir(base)):
        db_path = os.path.join(base, entry, "Msg", "MSG0.db")
        if os.path.exists(db_path):
            return entry, db_path

    print("[!] Cannot find WeChat database. Please make sure WeChat is logged in.")
    sys.exit(1)


# ── Contact lookup ────────────────────────────────────────────────

def find_chat_id(name: str) -> str | None:
    """Find a conversation ID by contact name (fuzzy). Returns wxid or chatroom ID."""
    sys.path.insert(0, SCRIPT_DIR)
    from core.decrypt import query_contact

    rows = query_contact(
        f"SELECT UserName, NickName, Remark FROM Contact "
        f"WHERE NickName LIKE '%{name}%' OR Remark LIKE '%{name}%' LIMIT 5;"
    )
    for row in rows:
        if len(row) >= 3:
            wxid, nick, remark = row[0].strip(), row[1].strip(), row[2].strip()
            display = remark or nick or wxid
            print(f"  Found: {display} ({wxid})")
            return wxid
    print(f"  [!] Cannot find \"{name}\".")
    return None


# ── API key ───────────────────────────────────────────────────────

def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    keyfile = os.path.join(PROJECT_ROOT, ".apikey")
    if os.path.exists(keyfile):
        return open(keyfile).read().strip()
    print("[!] Missing ANTHROPIC_API_KEY. Run:")
    print("    echo sk-ant-... > .apikey")
    sys.exit(1)


# ── Watch list ────────────────────────────────────────────────────

def get_watch_list(args: list[str], wxid: str) -> list[str]:
    """Resolve conversation names to IDs from CLI args or .watch file."""
    names = list(args)
    if not names:
        watchfile = os.path.join(PROJECT_ROOT, ".watch")
        if os.path.exists(watchfile):
            names = [l.strip() for l in open(watchfile, encoding="utf-8")
                     if l.strip() and not l.startswith("#")]
        else:
            print("[!] No conversations specified. Either:")
            print("    python start.py \"contact or group name\"")
            print("    or create a .watch file with one name per line")
            sys.exit(1)

    watch_ids = []
    for name in names:
        if "@chatroom" in name or name.startswith("wxid_"):
            watch_ids.append(name)
            print(f"  OK: {name}")
        else:
            chat_id = find_chat_id(name)
            if chat_id:
                watch_ids.append(chat_id)
    return watch_ids


# ── Pre-vectorize ─────────────────────────────────────────────────

def _auto_vectorize(watch_ids: list[str], env: dict):
    """Vectorize message history before starting the bot (backfill missing only)."""
    for k, v in env.items():
        os.environ[k] = v

    sys.path.insert(0, SCRIPT_DIR)
    from core.decrypt import query
    from core.reader import extract_text
    from core.embeddings import store, count, VECTOR_DB
    from core.contacts import preload_from_messages, get_name
    from config import MAX_HISTORY
    import sqlite3 as _sq

    for wid in watch_ids:
        # For Windows: query MSG table filtered by StrTalker
        try:
            total_rows = query(f"SELECT COUNT(*) FROM MSG WHERE StrTalker='{wid}' AND Type=1;")
            total = int(total_rows[0][0]) if total_rows and total_rows[0][0] else 0
        except Exception:
            continue

        # Already-vectorized IDs
        try:
            vconn = _sq.connect(VECTOR_DB)
            existing_ids = set(
                r[0] for r in vconn.execute(
                    "SELECT local_id FROM message_vectors WHERE table_name=?", (wid,)
                ).fetchall()
            )
            vconn.close()
        except Exception:
            existing_ids = set()

        rows = query(
            f"SELECT localId, CreateTime, IsSender, StrContent "
            f"FROM MSG WHERE StrTalker='{wid}' AND Type=1 "
            f"ORDER BY CreateTime DESC LIMIT 5000 OFFSET {MAX_HISTORY};"
        )
        missing = []
        for r in rows:
            try:
                if int(r[0]) not in existing_ids:
                    missing.append(r)
            except (ValueError, IndexError):
                pass

        if not missing:
            print(f"  OK {wid[:30]} vector store up to date ({len(existing_ids)} entries)")
            continue

        print(f"  Vectorizing {wid[:30]}: {len(missing)} new entries...")
        preload_from_messages(wid)
        done = 0
        for r in missing:
            try:
                text = extract_text(r[3])
                if not text or text.startswith("<") or len(text.strip()) < 3:
                    continue
                # Windows: IsSender=1 → me, IsSender=0 → other
                sender = "Me" if int(r[2]) == 1 else get_name(wid)
                store(wid, int(r[0]), text, sender, int(r[1]))
                done += 1
            except Exception:
                pass
        print(f"  Done {wid[:30]} ({done} new, {len(existing_ids)+done} total)")


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("WeChat AI Bot (Windows) starting...\n")

    keys_file = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
    if not os.path.exists(keys_file):
        print("[!] keys/wechat_keys.json not found.")
        print("    Run first:  python scripts/keys/extract_key_windows.py")
        sys.exit(1)

    wxid, db_path = detect_wxid_and_db()
    contact_db = os.path.join(os.path.dirname(db_path), "MicroMsg.db")
    print(f"Account : {wxid}")
    print(f"MSG DB  : {db_path}\n")

    print("Resolving conversation list...")
    sys.path.insert(0, SCRIPT_DIR)

    # Set environment so config.py resolves paths correctly
    os.environ.setdefault("WECHAT_MY_WXID", wxid)
    os.environ.setdefault("WECHAT_DB_PATH", db_path)
    os.environ.setdefault("WECHAT_CONTACT_DB", contact_db)

    watch_ids = get_watch_list(sys.argv[1:], wxid)
    if not watch_ids:
        print("[!] No valid conversations found.")
        sys.exit(1)

    api_key = get_api_key()

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key
    env["WECHAT_MY_WXID"]    = wxid
    env["WECHAT_DB_PATH"]    = db_path
    env["WECHAT_CONTACT_DB"] = contact_db
    env["WECHAT_WATCH_IDS"]  = ",".join(watch_ids)

    _auto_vectorize(watch_ids, env)

    print(f"\nReady — watching {len(watch_ids)} conversation(s). Starting bot...\n")
    bot_path = os.path.join(SCRIPT_DIR, "bot.py")
    os.execve(sys.executable, [sys.executable, "-u", bot_path], env)


if __name__ == "__main__":
    main()
