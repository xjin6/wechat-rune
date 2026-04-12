"""直接查询加密数据库——无需整体解密"""
import json, os, subprocess
from config import KEYS_FILE, WECHAT_DB_PATH, SQLCIPHER_BIN


_key = None

def get_key() -> str:
    global _key
    if _key is None:
        keys = json.load(open(KEYS_FILE))
        _key = keys.get("magicxinjx_c092/db_storage/message/message_0.db", "")
    return _key


def query(sql_body: str) -> list[tuple]:
    """在加密DB上执行查询，返回结果行列表"""
    key = get_key()
    sql_file = '/tmp/wechat_query.sql'
    with open(sql_file, 'w') as f:
        f.write('PRAGMA key = "x\'%s\'";\n' % key)
        f.write('PRAGMA cipher_page_size = 4096;\n')
        f.write('.separator "|||"\n')
        f.write(sql_body + '\n')

    r = subprocess.run(
        [SQLCIPHER_BIN, WECHAT_DB_PATH],
        stdin=open(sql_file),
        capture_output=True, text=True, timeout=10
    )
    rows = []
    for line in r.stdout.splitlines():
        if not line or line == 'ok':
            continue
        rows.append(tuple(line.split('|||')))
    return rows


def db_mtime() -> float:
    return os.path.getmtime(WECHAT_DB_PATH) if os.path.exists(WECHAT_DB_PATH) else 0
