"""
WeChat AI Bot configuration

All sensitive values are passed via environment variables; do not hard-code them here.
Copy .env.example to .env, fill in real values, then source .env before starting.
"""
import os
import hashlib

# ── Identity ─────────────────────────────────────────────────────
# Your WeChat wxid (visible in the DB path after login, e.g. magicxinjx from magicxinjx_c092)
MY_WXID = os.environ.get("WECHAT_MY_WXID", "your_wxid_here")

# ── Watched conversations ────────────────────────────────────────
# Group ID (xxxxx@chatroom) or personal wxid, comma-separated
# Example: export WECHAT_WATCH_IDS="12345678@chatroom,wxid_xxxxxxxx"
_watch_env = os.environ.get("WECHAT_WATCH_IDS", "")
WATCH_IDS = [w.strip() for w in _watch_env.split(",") if w.strip()] if _watch_env else []
WATCH_TABLES = ["Msg_" + hashlib.md5(wid.encode()).hexdigest() for wid in WATCH_IDS]

# ── Trigger words ────────────────────────────────────────────────
# Messages from others containing these words will trigger the bot; self can only trigger via /xin
BOT_TRIGGERS = os.environ.get("BOT_TRIGGERS", "小昕,/xin").split(",")

# ── Other (defined before AI_SYSTEM_PROMPT because the prompt references MAX_HISTORY) ──
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

# ── Paths (auto-generated from your WeChat account; can be overridden via env vars) ──
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))   # scripts/
PROJECT_ROOT  = os.path.dirname(SCRIPT_DIR)                  # project root
KEYS_FILE     = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
DB_DIR        = os.path.join(PROJECT_ROOT, "db")
DECRYPTED_DB  = os.path.join(DB_DIR, "message_0.db")
SQLCIPHER_BIN = os.environ.get("SQLCIPHER_BIN", "/opt/homebrew/opt/sqlcipher/bin/sqlcipher")

# WeChat database path (contains your wxid; replace as needed)
# Format: ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>_xxxx/db_storage/message/message_0.db
WECHAT_DB_PATH = os.environ.get(
    "WECHAT_DB_PATH",
    os.path.expanduser(f"~/Library/Containers/com.tencent.xinWeChat/Data/Documents"
                       f"/xwechat_files/{MY_WXID}_c092/db_storage/message/message_0.db")
)
WECHAT_WAL_PATH = WECHAT_DB_PATH + "-wal"

