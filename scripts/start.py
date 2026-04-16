#!/usr/bin/env python3
"""
WeChat AI Bot launcher (Windows — Weixin xwechat_files format)

Usage:
    python start.py                    # Start with conversation list from .watch
    python start.py "SSCI Team" John   # Specify conversation names on the fly
"""

import os, sys, json, hashlib, glob, sqlite3, tempfile, subprocess

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)


# ── Auto-detect WeChat account ────────────────────────────────────

def detect_wxid_and_db() -> tuple[str, str]:
    """Return (wxid, message_0.db_path) for the first active Weixin account."""
    from config import XWECHAT_FILES
    if not XWECHAT_FILES or not os.path.isdir(XWECHAT_FILES):
        print("[!] Cannot find xwechat_files directory.")
        print("    Set XWECHAT_FILES env var to the full path.")
        sys.exit(1)

    # Look for <wxid>_xxxx/db_storage/message/message_0.db
    pattern = os.path.join(XWECHAT_FILES, "*", "db_storage", "message", "message_0.db")
    matches = glob.glob(pattern)
    if not matches:
        print(f"[!] No message_0.db found under {XWECHAT_FILES}")
        print("    Please log in to Weixin and sync some messages first.")
        sys.exit(1)

    db_path   = matches[0]
    folder    = db_path.replace(XWECHAT_FILES, '').lstrip('/\\').split(os.sep)[0]
    wxid      = folder.split('_')[0]
    return wxid, db_path


# ── Contact lookup ────────────────────────────────────────────────

def find_chat_id(name: str, db_path: str, keys: dict) -> str | None:
    from config import SQLCIPHER_BIN
    contact_db = db_path.replace(
        os.sep + "message" + os.sep + "message_0.db",
        os.sep + "contact" + os.sep + "contact.db"
    )
    key = next((v for k, v in keys.items()
                if "contact/contact.db" in k.replace("\\", "/")), "")
    if not key or not os.path.exists(contact_db):
        print(f"  [!] Cannot access contact.db")
        return None

    sql = (f"SELECT username, nick_name, remark FROM contact "
           f"WHERE nick_name LIKE '%{name}%' OR remark LIKE '%{name}%' LIMIT 5;")

    # Try sqlcipher3 first
    try:
        import sqlcipher3 as _sc
        conn = _sc.connect(contact_db)
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        conn.execute("PRAGMA cipher_page_size = 4096")
        rows = conn.execute(sql).fetchall()
        conn.close()
        for row in rows:
            if len(row) >= 3:
                wxid, nick, remark = str(row[0]), str(row[1]), str(row[2])
                display = remark.strip() or nick.strip() or wxid.strip()
                print(f"  Found: {display} ({wxid.strip()})")
                return wxid.strip()
    except Exception:
        pass

    # Binary fallback
    if not os.path.exists(SQLCIPHER_BIN):
        print("  [!] sqlcipher3 not installed and SQLCIPHER_BIN not found.")
        return None

    fd, tmp = tempfile.mkstemp(suffix='.sql')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(f'PRAGMA key = "x\'{key}\'";\n')
            f.write('PRAGMA cipher_page_size = 4096;\n')
            f.write('.separator "|||"\n')
            f.write(sql + '\n')
        r = subprocess.run([SQLCIPHER_BIN, contact_db],
                           stdin=open(tmp, encoding='utf-8'),
                           capture_output=True, text=True, timeout=5)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    for line in r.stdout.splitlines():
        if '|||' in line:
            parts = line.split('|||')
            wxid, nick, remark = parts[0], parts[1], parts[2]
            display = remark.strip() or nick.strip()
            print(f"  Found: {display} ({wxid.strip()})")
            return wxid.strip()

    print(f"  [!] Cannot find \"{name}\"")
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

def get_watch_list(args: list[str], wxid: str, db_path: str, keys: dict) -> list[str]:
    names = list(args)
    if not names:
        watchfile = os.path.join(PROJECT_ROOT, ".watch")
        if os.path.exists(watchfile):
            names = [l.strip() for l in open(watchfile, encoding='utf-8')
                     if l.strip() and not l.startswith('#')]
        else:
            print("[!] No conversations specified. Either:")
            print("    python start.py \"contact or group name\"")
            print("    or create .watch with one name per line")
            sys.exit(1)

    watch_ids = []
    for name in names:
        if "@chatroom" in name or name.startswith("wxid_"):
            watch_ids.append(name)
            print(f"  OK: {name}")
        else:
            chat_id = find_chat_id(name, db_path, keys)
            if chat_id:
                watch_ids.append(chat_id)
    return watch_ids


# ── Pre-vectorize ─────────────────────────────────────────────────

def _auto_vectorize(watch_ids: list[str], db_path: str, keys: dict, env: dict):
    for k, v in env.items():
        os.environ[k] = v

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
        missing = [r for r in rows if r[0] and int(r[0]) not in existing_ids]

        if not missing:
            print(f"  OK {wid[:30]} vector store up to date ({len(existing_ids)} entries)")
            continue

        print(f"  Vectorizing {wid[:30]}: {len(missing)} new entries...")
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
        print(f"  Done {wid[:30]} ({done} new, {len(existing_ids)+done} total)")


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("Weixin AI Bot (Windows) starting...\n")

    keys_file = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
    if not os.path.exists(keys_file):
        print("[!] keys/wechat_keys.json not found.")
        print("    Run first (as Administrator):")
        print("    python scripts\\keys\\extract_key_windows.py")
        sys.exit(1)
    keys = json.load(open(keys_file))

    wxid, db_path = detect_wxid_and_db()
    print(f"Account : {wxid}")
    print(f"MSG DB  : {db_path}\n")

    print("Resolving conversation list...")
    watch_ids = get_watch_list(sys.argv[1:], wxid, db_path, keys)
    if not watch_ids:
        print("[!] No valid conversations found.")
        sys.exit(1)

    api_key = get_api_key()

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key
    env["WECHAT_MY_WXID"]    = wxid
    env["WECHAT_DB_PATH"]    = db_path
    env["WECHAT_WATCH_IDS"]  = ",".join(watch_ids)

    _auto_vectorize(watch_ids, db_path, keys, env)

    print(f"\nReady — watching {len(watch_ids)} conversation(s). Starting bot...\n")
    bot_path = os.path.join(SCRIPT_DIR, "bot.py")
    os.execve(sys.executable, [sys.executable, "-u", bot_path], env)


if __name__ == "__main__":
    main()
