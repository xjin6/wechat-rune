"""
Query WeChat databases on Windows.

Priority:
  1. Pre-decrypted local copy (fastest, no SQLCipher needed)
  2. sqlcipher3-binary Python package  (pip install sqlcipher3-binary)
  3. sqlcipher.exe binary              (set SQLCIPHER_BIN env var)

Windows WeChat uses SQLCipher 4 with HMAC_SHA1 by default.
Some accounts/versions use HMAC_SHA512 (Mac-style); both are tried automatically.
"""
import json, os, sqlite3, subprocess, tempfile
from config import (
    KEYS_FILE, WECHAT_DB_PATH, WECHAT_CONTACT_DB, SQLCIPHER_BIN,
    DECRYPTED_MSG_DB, DECRYPTED_CONTACT_DB
)

_msg_key: str     = None
_contact_key: str = None


def _load_keys():
    global _msg_key, _contact_key
    try:
        keys = json.load(open(KEYS_FILE))
        for k, v in keys.items():
            k_lower = k.lower()
            if "msg0.db" in k_lower or "message_0.db" in k_lower:
                _msg_key = v
            elif "micromsg.db" in k_lower or "contact.db" in k_lower:
                _contact_key = v
    except Exception:
        pass


def _get_msg_key() -> str:
    global _msg_key
    if _msg_key is None:
        _load_keys()
    return _msg_key or ""


def _get_contact_key() -> str:
    global _contact_key
    if _contact_key is None:
        _load_keys()
    return _contact_key or ""


# ── SQLCipher execution helpers ───────────────────────────────────

def _try_sqlcipher3(db_path: str, key: str, sql: str, sha1: bool = True) -> list[tuple] | None:
    """Try to query using the sqlcipher3 Python package. Returns None if unavailable."""
    try:
        import sqlcipher3 as _sc
    except ImportError:
        return None

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
        return None


def _try_binary(db_path: str, key: str, sql: str, sha1: bool = True) -> list[tuple]:
    """Query via sqlcipher.exe binary."""
    if not os.path.exists(SQLCIPHER_BIN):
        return []
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
        if not line or line.strip() == 'ok':
            continue
        rows.append(tuple(line.split('|||')))
    return rows


def _sqlcipher_query(db_path: str, key: str, sql: str) -> list[tuple]:
    """Execute SQL against an encrypted SQLCipher database.
    Tries HMAC_SHA1 first (Windows default), then HMAC_SHA512 (Mac / some Windows versions).
    """
    # SHA1 pass
    result = _try_sqlcipher3(db_path, key, sql, sha1=True)
    if result is not None:
        return result

    # SHA512 pass (try sqlcipher3 with default params)
    result = _try_sqlcipher3(db_path, key, sql, sha1=False)
    if result is not None:
        return result

    # Binary fallback — SHA1
    result = _try_binary(db_path, key, sql, sha1=True)
    if result:
        return result

    # Binary fallback — SHA512
    return _try_binary(db_path, key, sql, sha1=False)


# ── Public query functions ────────────────────────────────────────

def query(sql_body: str) -> list[tuple]:
    """Query MSG0.db (messages). Prefers the pre-decrypted local copy."""
    if os.path.exists(DECRYPTED_MSG_DB):
        try:
            conn = sqlite3.connect(DECRYPTED_MSG_DB)
            rows = conn.execute(sql_body.strip().rstrip(';')).fetchall()
            conn.close()
            return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
        except Exception:
            pass
    key = _get_msg_key()
    if not key:
        return []
    return _sqlcipher_query(WECHAT_DB_PATH, key, sql_body)


def query_contact(sql_body: str) -> list[tuple]:
    """Query MicroMsg.db (contacts / groups). Prefers the pre-decrypted local copy."""
    if os.path.exists(DECRYPTED_CONTACT_DB):
        try:
            conn = sqlite3.connect(DECRYPTED_CONTACT_DB)
            rows = conn.execute(sql_body.strip().rstrip(';')).fetchall()
            conn.close()
            return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
        except Exception:
            pass
    key = _get_contact_key()
    if not key:
        return []
    return _sqlcipher_query(WECHAT_CONTACT_DB, key, sql_body)


def db_mtime() -> float:
    """Return modification time of the main message database."""
    return os.path.getmtime(WECHAT_DB_PATH) if os.path.exists(WECHAT_DB_PATH) else 0
