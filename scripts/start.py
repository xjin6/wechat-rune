#!/usr/bin/env python3.9
"""
WeChat AI Bot launcher

Usage:
    python3.9 start.py                   # Start with conversation list from .watch
    python3.9 start.py "SSCI Team" HK    # Specify conversation names on the fly
"""

import os, sys, subprocess, json, hashlib, glob, sqlite3

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# ── Auto-detect wxid and DB path ─────────────────────────────────

def detect_wxid_and_db():
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    # Look for *_c092 style directories
    pattern = os.path.join(base, "*_c092", "db_storage", "message", "message_0.db")
    matches = glob.glob(pattern)
    if not matches:
        # Also try other suffixes
        matches = glob.glob(os.path.join(base, "*", "db_storage", "message", "message_0.db"))
    if not matches:
        print("❌ Cannot find WeChat database. Please make sure WeChat is logged in.")
        sys.exit(1)
    db_path = matches[0]
    # Extract wxid from path
    wxid_part = db_path.split("/xwechat_files/")[1].split("/")[0]
    wxid = wxid_part.split("_")[0] if "_" in wxid_part else wxid_part
    return wxid, db_path


# ── Look up conversation ID from contact.db/session.db ───────────

def find_chat_id(name: str, db_path: str, keys: dict) -> str:
    """Find conversation ID by name (nickname/remark/group name), return wxid or chatroom ID"""
    sqlcipher = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"

    # Query contact.db (contacts + groups)
    contact_db = db_path.replace("/message/message_0.db", "/contact/contact.db")
    key = next((v for k, v in keys.items() if "contact/contact.db" in k), "")
    if key and os.path.exists(contact_db):
        sql_file = "/tmp/find_chat.sql"
        with open(sql_file, "w") as f:
            f.write('PRAGMA key = "x\'%s\'";\n' % key)
            f.write('PRAGMA cipher_page_size = 4096;\n')
            f.write('.separator "|||"\n')
            f.write(f"SELECT username, nick_name, remark FROM contact "
                    f"WHERE nick_name LIKE '%{name}%' OR remark LIKE '%{name}%' LIMIT 5;\n")
        r = subprocess.run([sqlcipher, contact_db], stdin=open(sql_file),
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "|||" in line:
                parts = line.split("|||")
                wxid, nick, remark = parts[0], parts[1], parts[2]
                display = remark or nick
                print(f"  ✓ Found: {display} ({wxid})")
                return wxid

    print(f"  ❌ Cannot find \"{name}\". Please check the name.")
    return None


# ── Read API Key ─────────────────────────────────────────────────

def get_api_key():
    # 1. Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    # 2. Local .apikey file
    keyfile = os.path.join(PROJECT_ROOT, ".apikey")
    if os.path.exists(keyfile):
        return open(keyfile).read().strip()
    print("❌ Missing ANTHROPIC_API_KEY. Please run:")
    print("   echo 'sk-ant-...' > .apikey")
    sys.exit(1)


# ── Read default watch list ──────────────────────────────────────

def get_watch_list(args, wxid, db_path, keys):
    """Get conversation list from CLI args or .watch file, return [wxid/chatroom_id, ...]"""
    names = list(args)

    if not names:
        watchfile = os.path.join(PROJECT_ROOT, ".watch")
        if os.path.exists(watchfile):
            names = [l.strip() for l in open(watchfile) if l.strip() and not l.startswith("#")]
        else:
            print("❌ No conversations specified. Please either:")
            print("   python3.9 start.py \"group or contact name\"")
            print("   or create a .watch file with one name per line")
            sys.exit(1)

    watch_ids = []
    for name in names:
        # Already in ID format (contains @ or wxid_)
        if "@chatroom" in name or name.startswith("wxid_"):
            watch_ids.append(name)
            print(f"  ✓ {name}")
        else:
            chat_id = find_chat_id(name, db_path, keys)
            if chat_id:
                watch_ids.append(chat_id)

    return watch_ids


# ── Main flow ────────────────────────────────────────────────────

def main():
    print("🤖 WeChat AI Bot starting...\n")

    # Load decryption keys
    keys_file = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
    if not os.path.exists(keys_file):
        print("❌ Cannot find keys/wechat_keys.json. Please extract decryption keys first.")
        sys.exit(1)
    keys = json.load(open(keys_file))

    # Auto-detect
    wxid, db_path = detect_wxid_and_db()
    print(f"✓ Account: {wxid}")
    print(f"✓ DB: {db_path}\n")

    # Get watch list
    print("📋 Resolving conversation list...")
    watch_ids = get_watch_list(sys.argv[1:], wxid, db_path, keys)

    if not watch_ids:
        print("❌ No valid conversations")
        sys.exit(1)

    api_key = get_api_key()

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key
    env["WECHAT_MY_WXID"] = wxid
    env["WECHAT_DB_PATH"] = db_path
    env["WECHAT_WATCH_IDS"] = ",".join(watch_ids)

    # Vectorize first (finish before starting bot)
    _auto_vectorize(watch_ids, db_path, keys, env)

    print(f"\n✅ Ready to watch {len(watch_ids)} conversations, starting...\n")
    bot_path = os.path.join(SCRIPT_DIR, "bot.py")
    os.execve(sys.executable, [sys.executable, "-u", bot_path], env)


def _auto_vectorize(watch_ids: list, db_path: str, keys: dict, env: dict):
    """Pre-launch vectorization of history messages for each watched conversation (compare by local_id, only backfill missing)"""
    for k, v in env.items():
        os.environ[k] = v

    import sys as _sys
    _sys.path.insert(0, os.path.dirname(__file__))
    from core.decrypt import query
    from core.reader import extract_text
    from core.embeddings import store, count, VECTOR_DB
    from core.contacts import preload_from_messages, get_name
    from core.ai import _extract_sender_wxid
    from config import MAX_HISTORY
    import sqlite3 as _sq

    for wid in watch_ids:
        table = "Msg_" + hashlib.md5(wid.encode()).hexdigest()

        try:
            total_rows = query(f"SELECT COUNT(*) FROM {table} WHERE local_type=1;")
            total = int(total_rows[0][0]) if total_rows and total_rows[0][0] else 0
        except Exception:
            continue

        # Set of already-vectorized local_ids
        try:
            vconn = _sq.connect(VECTOR_DB)
            existing_ids = set(
                r[0] for r in vconn.execute(
                    "SELECT local_id FROM message_vectors WHERE table_name=?", (table,)
                ).fetchall()
            )
            vconn.close()
        except Exception:
            existing_ids = set()

        rows = query(
            f"SELECT local_id, create_time, real_sender_id, hex(message_content) "
            f"FROM {table} WHERE local_type=1 "
            f"ORDER BY create_time DESC LIMIT 5000 OFFSET {MAX_HISTORY};"
        )
        missing = []
        for r in rows:
            try:
                if int(r[0]) not in existing_ids:
                    missing.append(r)
            except (ValueError, IndexError):
                pass

        if not missing:
            print(f"  ✓ {wid[:30]} vector store up to date ({len(existing_ids)} entries), skipping")
            continue

        print(f"  🔄 {wid[:30]} vectorizing {len(missing)} new entries...")
        preload_from_messages(table)
        done = 0
        for r in missing:
            try:
                text = extract_text(r[3])
                if not text or text.startswith("<") or len(text.strip()) < 3:
                    continue
                wxid_sender = _extract_sender_wxid(r[3])
                sender = get_name(wxid_sender) if wxid_sender else ("Me" if int(r[2]) == 1 else "?")
                store(table, int(r[0]), text, sender, int(r[1]))
                done += 1
            except Exception:
                pass
        print(f"  ✅ {wid[:30]} done ({done} new, {len(existing_ids)+done} total)")


if __name__ == "__main__":
    main()
