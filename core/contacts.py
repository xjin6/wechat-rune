"""按需查询联系人名字，只缓存实际遇到的人"""
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
    """查一个wxid的显示名（备注>昵称），结果缓存"""
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

    _cache[wxid] = wxid  # 找不到就用wxid本身
    return wxid


def find_wxid(name: str):
    """按昵称或备注反查 wxid（模糊匹配）"""
    # 先查缓存
    for wxid, cached_name in _cache.items():
        parts = re.split(r'[（(）)]', cached_name)
        if any(name in p for p in parts):
            return wxid

    # 缓存没有，查 DB
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
    """扫描对话里出现过的所有发言者，批量加载进缓存。
    Bot启动时对每个监听对话调用一次，之后所有名字查询都走缓存。"""
    from core.decrypt import query as db_query
    import re as _re

    # 拉取最近消息的原始内容，找发言者wxid
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
    """批量预加载一组wxid，减少后续单次查询"""
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
                # 备注和昵称都有且不同时，显示"备注(昵称)"方便Claude识别
                if remark and nick and remark != nick:
                    _cache[wxid] = f"{remark}({nick})"
                else:
                    _cache[wxid] = remark or nick or wxid
                found.add(wxid)
    # 找不到的也缓存，避免重复查
    for w in missing:
        if w not in found:
            _cache[w] = w
