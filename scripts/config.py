"""
WeChat AI Bot configuration (Windows)

All sensitive values are passed via environment variables; do not hard-code them here.
Copy .env.example to .env, fill in real values, then run: set /p < .env  (or use dotenv)
"""
import os
import hashlib

# ── Identity ─────────────────────────────────────────────────────
# Your WeChat wxid (the folder name under WeChat Files, e.g. "wxid_xxxxxxxx")
MY_WXID = os.environ.get("WECHAT_MY_WXID", "your_wxid_here")

# ── Watched conversations ────────────────────────────────────────
# Group ID (xxxxx@chatroom) or personal wxid, comma-separated
_watch_env = os.environ.get("WECHAT_WATCH_IDS", "")
WATCH_IDS = [w.strip() for w in _watch_env.split(",") if w.strip()] if _watch_env else []
# Windows: "tables" are conversation IDs (StrTalker values), not DB table names
WATCH_TABLES = WATCH_IDS

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
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))   # scripts/
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)                  # project root
KEYS_FILE    = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
DB_DIR       = os.path.join(PROJECT_ROOT, "db")

# Decrypted DB cache paths (written by decrypt_db.py, used for fast queries)
DECRYPTED_MSG_DB     = os.path.join(DB_DIR, "MSG0.db")
DECRYPTED_CONTACT_DB = os.path.join(DB_DIR, "MicroMsg.db")

# Windows WeChat data root: %USERPROFILE%\Documents\WeChat Files\<wxid>\
WECHAT_FILES_ROOT = os.environ.get(
    "WECHAT_FILES_ROOT",
    os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files")
)

# Main message database — watch this for new messages
WECHAT_DB_PATH = os.environ.get(
    "WECHAT_DB_PATH",
    os.path.join(WECHAT_FILES_ROOT, MY_WXID, "Msg", "MSG0.db")
)
WECHAT_WAL_PATH = WECHAT_DB_PATH + "-wal"

# Contact / group info database
WECHAT_CONTACT_DB = os.environ.get(
    "WECHAT_CONTACT_DB",
    os.path.join(WECHAT_FILES_ROOT, MY_WXID, "Msg", "MicroMsg.db")
)

# SQLCipher binary path (fallback if sqlcipher3-binary package not installed)
# Download: https://github.com/nalgeon/sqlean/releases  or  choco install sqlcipher
SQLCIPHER_BIN = os.environ.get("SQLCIPHER_BIN", r"C:\sqlcipher\sqlcipher.exe")
