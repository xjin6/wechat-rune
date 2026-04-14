"""Query the database -- prefer the local decrypted copy, fall back to sqlcipher"""
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
    """Query the message database. Prefers the decrypted local copy (faster), otherwise uses sqlcipher"""
    # Prefer the decrypted copy
    if os.path.exists(DECRYPTED_DB):
        try:
            conn = sqlite3.connect(DECRYPTED_DB)
            # Execute a single SQL statement (possibly a SELECT) directly
            sql = sql_body.strip().rstrip(';')
            rows = conn.execute(sql).fetchall()
            conn.close()
            # Convert to string tuples for compatibility
            return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
        except Exception:
            pass

    # Fallback: query the encrypted DB via sqlcipher
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
