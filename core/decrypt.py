"""查询数据库——优先用本地解密副本，回退到sqlcipher"""
import json, os, subprocess, sqlite3, tempfile
from config import KEYS_FILE, WECHAT_DB_PATH, SQLCIPHER_BIN, DECRYPTED_DB

_key = None

def get_key() -> str:
    global _key
    if _key is None:
        try:
            keys = json.load(open(KEYS_FILE))
            _key = next((v for k, v in keys.items() if "message/message_0.db" in k), "")
        except Exception:
            _key = ""
    return _key


def query(sql_body: str) -> list[tuple]:
    """查询消息数据库。优先使用已解密的本地副本（速度快），否则用 sqlcipher"""
    # 优先使用已解密的副本
    if os.path.exists(DECRYPTED_DB):
        try:
            conn = sqlite3.connect(DECRYPTED_DB)
            # 将单个SQL语句（可能含 SELECT）直接执行
            sql = sql_body.strip().rstrip(';')
            rows = conn.execute(sql).fetchall()
            conn.close()
            # 转成字符串元组以保持兼容性
            return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
        except Exception:
            pass

    # 回退：用 sqlcipher 查加密 DB
    key = get_key()
    if not key:
        return []
    fd, sql_file = tempfile.mkstemp(suffix='.sql', prefix='wq_')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write('PRAGMA key = "x\'%s\'";\n' % key)
            f.write('PRAGMA cipher_page_size = 4096;\n')
            f.write('.separator "|||"\n')
            f.write(sql_body + '\n')
        r = subprocess.run([SQLCIPHER_BIN, WECHAT_DB_PATH],
                           stdin=open(sql_file), capture_output=True, text=True, timeout=10)
    finally:
        os.unlink(sql_file)
    rows = []
    for line in r.stdout.splitlines():
        if not line or line == 'ok':
            continue
        rows.append(tuple(line.split('|||')))
    return rows


def db_mtime() -> float:
    return os.path.getmtime(WECHAT_DB_PATH) if os.path.exists(WECHAT_DB_PATH) else 0
