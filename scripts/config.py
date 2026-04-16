"""
WeChat AI Bot configuration (Windows — Weixin xwechat_files format)

The Windows Weixin app uses the SAME database schema as Mac WeChat.
Key differences from Mac:
  - Data path: search for xwechat_files directory on any Windows drive
  - Key extraction: use extract_key_windows.py (ReadProcessMemory)
  - Message sending: use Win32 API instead of AppleScript

All sensitive values are passed via environment variables.
Copy .env.example to .env, fill in values, load with: python-dotenv or set commands.
"""
import os
import hashlib
import ctypes
import string


def _find_xwechat_files() -> str:
    """
    Auto-detect the xwechat_files root directory on any Windows drive.
    Checks: registry FileSavePath → common locations → full drive search.
    """
    # 1. Check registry for user-configured path
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key_path in (r'Software\Tencent\WeChat', r'Software\Tencent\Weixin'):
                try:
                    key = winreg.OpenKey(hive, key_path)
                    for val_name in ('FileSavePath', 'FileStoragePath', 'DataPath'):
                        try:
                            val, _ = winreg.QueryValueEx(key, val_name)
                            candidate = os.path.join(val, 'xwechat_files')
                            if os.path.isdir(candidate):
                                winreg.CloseKey(key)
                                return candidate
                        except OSError:
                            pass
                    winreg.CloseKey(key)
                except OSError:
                    pass
    except ImportError:
        pass

    # 2. Common default locations
    common = [
        os.path.join(os.path.expanduser('~'), 'Documents', 'WeChat Files', 'xwechat_files'),
        os.path.join(os.path.expanduser('~'), 'Documents', 'xwechat_files'),
    ]
    for p in common:
        if os.path.isdir(p):
            return p

    # 3. Full drive search (capped to reasonable depth)
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if not (bitmask & 1):
                bitmask >>= 1
                continue
            bitmask >>= 1
            drive = letter + ':/'
            if not os.path.exists(drive):
                continue
            for root, dirs, _ in os.walk(drive):
                dirs[:] = [d for d in dirs
                           if d not in ('Windows', '$Recycle.Bin', 'ProgramData',
                                        'System Volume Information')]
                if 'xwechat_files' in dirs:
                    return os.path.join(root, 'xwechat_files')
                if root.count(os.sep) > 5:
                    dirs.clear()
    except Exception:
        pass

    return ""


# ── Identity ─────────────────────────────────────────────────────
MY_WXID = os.environ.get("WECHAT_MY_WXID", "your_wxid_here")

# ── Watched conversations ────────────────────────────────────────
_watch_env  = os.environ.get("WECHAT_WATCH_IDS", "")
WATCH_IDS   = [w.strip() for w in _watch_env.split(",") if w.strip()] if _watch_env else []
WATCH_TABLES = ["Msg_" + hashlib.md5(wid.encode()).hexdigest() for wid in WATCH_IDS]

# ── Trigger words ────────────────────────────────────────────────
BOT_TRIGGERS = os.environ.get("BOT_TRIGGERS", "小昕,/xin").split(",")

# ── Other ────────────────────────────────────────────────────────
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "100"))

# ── AI ───────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL      = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "1500"))
REPLY_PREFIX  = os.environ.get("REPLY_PREFIX", "👾 ")

AI_SYSTEM_PROMPT = (
    "You are an AI assistant. "
    f"You can see the last {MAX_HISTORY} messages of conversation history; make full use of context when answering. "
    "If asked what someone said, find and summarize it from the history directly; never say you cannot see the records. "
    "Carefully observe the user's own speaking style in the history: word choice, sentence length, tone, degree of code-switching between languages, and mirror that style in your replies. "
    "Judge the occasion from context; do not define yourself as an assistant for any specific group. "
    "Forbidden: suggestive emoji; cliches (great/awesome/I understand); ending with a question (anything else?/need help?); warm sign-offs (that's all/hope this helps); bullet-point formatting. "
    "For normal replies, do not break lines; write a few sentences in a row. Only use line breaks when listing multiple points. "
    "Stop when you are done; never add any closing sentence. Do not start replies with " + REPLY_PREFIX.strip() + "."
)

# ── Paths ────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
KEYS_FILE    = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
DB_DIR       = os.path.join(PROJECT_ROOT, "db")
DECRYPTED_DB = os.path.join(DB_DIR, "message_0.db")

# xwechat_files root — auto-detected, or override via env var
XWECHAT_FILES = os.environ.get("XWECHAT_FILES", _find_xwechat_files())

# WeChat database path (same structure as Mac: <wxid>_xxx/db_storage/message/message_0.db)
_default_db = os.path.join(XWECHAT_FILES, f"{MY_WXID}_c092",
                           "db_storage", "message", "message_0.db")
WECHAT_DB_PATH  = os.environ.get("WECHAT_DB_PATH", _default_db)
WECHAT_WAL_PATH = WECHAT_DB_PATH + "-wal"

# SQLCipher binary path (fallback if sqlcipher3-binary package not installed)
# choco install sqlcipher  OR  download from https://github.com/nalgeon/sqlean/releases
SQLCIPHER_BIN = os.environ.get("SQLCIPHER_BIN", r"C:\sqlcipher\sqlcipher.exe")
