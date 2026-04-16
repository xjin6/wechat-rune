"""
Query WeChat databases on Windows (xwechat_files schema — same as Mac).

Priority:
  1. Pre-decrypted local copy at db/message_0.db  (fastest)
  2. sqlcipher3-binary Python package              (pip install sqlcipher3-binary)
  3. sqlcipher.exe binary                          (set SQLCIPHER_BIN env var)

Windows Weixin uses the same SQLCipher 4 / HMAC-SHA512 as Mac WeChat.
SHA1 is tried as a fallback in case of version differences.
"""
import json, os, sqlite3, subprocess, tempfile
from config import KEYS_FILE, WECHAT_DB_PATH, SQLCIPHER_BIN, DECRYPTED_DB

_key = None


def get_key() -> str:
    global _key
    if _key is None:
        try:
            keys = json.load(open(KEYS_FILE))
            _key = next((v for k, v in keys.items() if "message/message_0.db" in k.replace("\\", "/")), "")
        except Exception:
            _key = ""
    return _key


def _sqlcipher_query(db_path: str, key: str, sql: str) -> list[tuple]:
    """Try sqlcipher3 package first (SHA512 then SHA1), fall back to binary."""
    try:
        import sqlcipher3 as _sc
    except ImportError:
        _sc = None

    if _sc:
        for sha1 in (False, True):
            try:
                conn = _sc.connect(db_path)
                conn.execute(f"PRAGMA key = \"x'{key}'\"")
                conn.execute("PRAGMA cipher_page_size = 4096")
                if sha1:
                    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
                    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1")
                rows = conn.execute(sql.strip().rstrip(';')).fetchall()
                conn.close()
                return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
            except Exception:
                pass

    # Binary fallback
    if not os.path.exists(SQLCIPHER_BIN):
        return []

    for sha1 in (False, True):
        fd, tmp = tempfile.mkstemp(suffix='.sql', prefix='wq_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(f'PRAGMA key = "x\'{key}\'";\n')
                f.write('PRAGMA cipher_page_size = 4096;\n')
                if sha1:
                    f.write('PRAGMA cipher_hmac_algorithm = HMAC_SHA1;\n')
                    f.write('PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1;\n')
                f.write('.separator "|||"\n')
                f.write(sql + '\n')
            r = subprocess.run(
                [SQLCIPHER_BIN, db_path],
                stdin=open(tmp, encoding='utf-8'),
                capture_output=True, text=True, timeout=10
            )
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        rows = []
        for line in r.stdout.splitlines():
            if line and line.strip() != 'ok':
                rows.append(tuple(line.split('|||')))
        if rows:
            return rows
    return []


def query(sql_body: str) -> list[tuple]:
    """Query message_0.db. Prefers the pre-decrypted local copy."""
    if os.path.exists(DECRYPTED_DB):
        try:
            conn = sqlite3.connect(DECRYPTED_DB)
            rows = conn.execute(sql_body.strip().rstrip(';')).fetchall()
            conn.close()
            return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
        except Exception:
            pass
    key = get_key()
    if not key:
        return []
    return _sqlcipher_query(WECHAT_DB_PATH, key, sql_body)


def query_db(db_path: str, key: str, sql_body: str) -> list[tuple]:
    """Query any encrypted database (used by export_chat and contacts)."""
    return _sqlcipher_query(db_path, key, sql_body)


def db_mtime() -> float:
    return os.path.getmtime(WECHAT_DB_PATH) if os.path.exists(WECHAT_DB_PATH) else 0
