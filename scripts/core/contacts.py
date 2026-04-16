"""Contact name lookup — xwechat_files schema (same as Mac WeChat).

Queries contact/contact.db using the same schema as the Mac version.
Windows fix: use tempfile instead of hardcoded /tmp paths.
"""
import json, os, re, tempfile, subprocess
from config import KEYS_FILE, SQLCIPHER_BIN

_cache: dict[str, str] = {}


def _get_db() -> str:
    from config import WECHAT_DB_PATH
    return WECHAT_DB_PATH.replace(
        "/message/message_0.db", "/contact/contact.db"
    ).replace(
        "\\message\\message_0.db", "\\contact\\contact.db"
    )


def _get_key() -> str:
    from config import MY_WXID
    keys = json.load(open(KEYS_FILE))
    for k, v in keys.items():
        if "contact/contact.db" in k.replace("\\", "/"):
            return v
    return ""


def _run_sql(sql: str) -> list[str]:
    """Execute SQL against contact.db, return raw output lines."""
    key = _get_key()
    if not key:
        return []

    # Try sqlcipher3 package first
    try:
        import sqlcipher3 as _sc
        for sha1 in (False, True):
            try:
                conn = _sc.connect(_get_db())
                conn.execute(f"PRAGMA key = \"x'{key}'\"")
                conn.execute("PRAGMA cipher_page_size = 4096")
                if sha1:
                    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
                    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1")
                rows = conn.execute(sql.strip().rstrip(';')).fetchall()
                conn.close()
                return ['|||'.join(str(c) if c else '' for c in r) for r in rows]
            except Exception:
                pass
    except ImportError:
        pass

    # Binary fallback — use tempfile for Windows compatibility
    if not os.path.exists(SQLCIPHER_BIN):
        return []
    fd, tmp = tempfile.mkstemp(suffix='.sql', prefix='wc_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('PRAGMA key = "x\'%s\'";\n' % key)
            f.write('PRAGMA cipher_page_size = 4096;\n')
            f.write('.separator "|||"\n')
            f.write(sql + '\n')
        r = subprocess.run(
            [SQLCIPHER_BIN, _get_db()],
            stdin=open(tmp, encoding='utf-8'),
            capture_output=True, text=True, timeout=5
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return [line for line in r.stdout.splitlines() if '|||' in line]


def get_name(wxid: str) -> str:
    if not wxid:
        return wxid
    if wxid in _cache:
        return _cache[wxid]

    lines = _run_sql(f"SELECT nick_name, remark FROM contact WHERE username = '{wxid}' LIMIT 1;")
    for line in lines:
        nick, remark = line.split('|||', 1)
        nick, remark = nick.strip(), remark.strip()
        if remark and nick and remark != nick:
            name = f"{remark}({nick})"
        else:
            name = remark or nick or wxid
        _cache[wxid] = name
        return name

    _cache[wxid] = wxid
    return wxid


def find_wxid(name: str) -> str | None:
    for wxid, cached_name in _cache.items():
        parts = re.split(r'[（(）)]', cached_name)
        if any(name in p for p in parts):
            return wxid

    lines = _run_sql(
        f"SELECT username, nick_name, remark FROM contact "
        f"WHERE nick_name LIKE '%{name}%' OR remark LIKE '%{name}%' LIMIT 1;"
    )
    for line in lines:
        parts = line.split('|||', 2)
        if len(parts) == 3:
            wxid, nick, remark = parts
            nick, remark = nick.strip(), remark.strip()
            if remark and nick and remark != nick:
                _cache[wxid.strip()] = f"{remark}({nick})"
            else:
                _cache[wxid.strip()] = remark or nick or wxid.strip()
            return wxid.strip()
    return None


def preload_from_messages(table: str, limit: int = 500):
    """Scan recent messages for sender wxids and bulk-load their names."""
    from core.decrypt import query as db_query
    import re as _re

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
    missing = [w for w in wxids if w not in _cache]
    if not missing:
        return
    ids_str = ','.join(f"'{w}'" for w in missing)
    lines = _run_sql(
        f"SELECT username, nick_name, remark FROM contact WHERE username IN ({ids_str});"
    )
    found = set()
    for line in lines:
        parts = line.split('|||', 2)
        if len(parts) == 3:
            wxid, nick, remark = parts
            nick, remark = nick.strip(), remark.strip()
            if remark and nick and remark != nick:
                _cache[wxid.strip()] = f"{remark}({nick})"
            else:
                _cache[wxid.strip()] = remark or nick or wxid.strip()
            found.add(wxid.strip())
    for w in missing:
        if w not in found:
            _cache[w] = w
