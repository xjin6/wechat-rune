"""Query contact names on demand, caching only those actually encountered"""
import subprocess, json, os, re
from config import KEYS_FILE, SQLCIPHER_BIN

_cache: dict[str, str] = {}


def _get_db() -> str:
    from config import WECHAT_DB_PATH
    # contact.db is in the same db_storage folder as message_0.db
    return WECHAT_DB_PATH.replace(
        "/message/message_0.db", "/contact/contact.db"
    )


def _get_key() -> str:
    from config import MY_WXID
    keys = json.load(open(KEYS_FILE))
    # Key path pattern: <wxid>_xxxx/db_storage/contact/contact.db
    for k, v in keys.items():
        if "contact/contact.db" in k:
            return v
    return ""


def get_name(wxid: str) -> str:
    """Look up display name for a wxid (remark > nickname), result is cached"""
    if not wxid:
        return wxid
    if wxid in _cache:
        return _cache[wxid]

    key = _get_key()
    with open('/tmp/contact_q.sql', 'w') as f:
        f.write('PRAGMA key = "x\'%s\'";\n' % key)
        f.write('PRAGMA cipher_page_size = 4096;\n')
        f.write('.separator "|||"\n')
        f.write(f"SELECT nick_name, remark FROM contact WHERE username = '{wxid}' LIMIT 1;\n")

    r = subprocess.run(
        [SQLCIPHER_BIN, _get_db()],
        stdin=open('/tmp/contact_q.sql'),
        capture_output=True, text=True, timeout=5
    )
    for line in r.stdout.splitlines():
        if '|||' in line:
            nick, remark = line.split('|||', 1)
            nick, remark = nick.strip(), remark.strip()
            if remark and nick and remark != nick:
                name = f"{remark}({nick})"
            else:
                name = remark or nick or wxid
            _cache[wxid] = name
            return name

    _cache[wxid] = wxid  # Not found; fall back to the wxid itself
    return wxid


def find_wxid(name: str):
    """Reverse-lookup wxid by nickname or remark (fuzzy match)"""
    # Check the cache first
    for wxid, cached_name in _cache.items():
        parts = re.split(r'[（(）)]', cached_name)
        if any(name in p for p in parts):
            return wxid

    # Not in cache; query the DB
    key = _get_key()
    with open('/tmp/contact_q.sql', 'w') as f:
        f.write('PRAGMA key = "x\'%s\'";\n' % key)
        f.write('PRAGMA cipher_page_size = 4096;\n')
        f.write('.separator "|||"\n')
        f.write(f"SELECT username, nick_name, remark FROM contact "
                f"WHERE nick_name LIKE '%{name}%' OR remark LIKE '%{name}%' LIMIT 1;\n")
    r = subprocess.run(
        [SQLCIPHER_BIN, _get_db()],
        stdin=open('/tmp/contact_q.sql'),
        capture_output=True, text=True, timeout=5
    )
    for line in r.stdout.splitlines():
        if '|||' in line:
            parts = line.split('|||', 2)
            if len(parts) == 3:
                wxid, nick, remark = parts
                nick, remark = nick.strip(), remark.strip()
                if remark and nick and remark != nick:
                    _cache[wxid] = f"{remark}({nick})"
                else:
                    _cache[wxid] = remark or nick or wxid
                return wxid.strip()
    return None


def preload_from_messages(table: str, limit: int = 500):
    """Scan all senders that appeared in the conversation and bulk-load them into cache.
    Called once per monitored chat at bot startup; all subsequent name lookups hit the cache."""
    from core.decrypt import query as db_query
    import re as _re

    # Fetch raw content of recent messages to find sender wxids
    rows = db_query(
        f"SELECT hex(message_content) FROM {table} "
        f"WHERE local_type = 1 ORDER BY create_time DESC LIMIT {limit};"
    )
    wxids = set()
    for r in rows:
        try:
            raw = bytes.fromhex(r[0]) if r[0] else b''
            import zstandard as _zstd
            if raw[:4] == b'\x28\xb5\x2f\xfd':
                text = _zstd.decompress(raw).decode('utf-8', errors='replace')
            else:
                text = raw.decode('utf-8', errors='replace')
            if '\n' in text:
                first = text.split('\n', 1)[0].strip().rstrip(':')
                if _re.match(r'^[\w]{4,30}$', first):
                    wxids.add(first)
        except Exception:
            pass

    if wxids:
        preload(list(wxids))


def preload(wxids: list[str]):
    """Bulk-preload a set of wxids to reduce subsequent individual queries"""
    missing = [w for w in wxids if w not in _cache]
    if not missing:
        return

    key = _get_key()
    ids_str = ','.join(f"'{w}'" for w in missing)
    with open('/tmp/contact_q.sql', 'w') as f:
        f.write('PRAGMA key = "x\'%s\'";\n' % key)
        f.write('PRAGMA cipher_page_size = 4096;\n')
        f.write('.separator "|||"\n')
        f.write(f"SELECT username, nick_name, remark FROM contact WHERE username IN ({ids_str});\n")

    r = subprocess.run(
        [SQLCIPHER_BIN, _get_db()],
        stdin=open('/tmp/contact_q.sql'),
        capture_output=True, text=True, timeout=5
    )
    found = set()
    for line in r.stdout.splitlines():
        if '|||' in line:
            parts = line.split('|||', 2)
            if len(parts) == 3:
                wxid, nick, remark = parts
                nick, remark = nick.strip(), remark.strip()
                # When remark and nickname both exist and differ, show "remark(nickname)" for Claude to recognize
                if remark and nick and remark != nick:
                    _cache[wxid] = f"{remark}({nick})"
                else:
                    _cache[wxid] = remark or nick or wxid
                found.add(wxid)
    # Cache misses too, to avoid repeated lookups
    for w in missing:
        if w not in found:
            _cache[w] = w
