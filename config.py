"""
微信AI机器人配置

所有敏感信息通过环境变量传入，不要硬编码在此文件里。
复制 .env.example 为 .env，填入实际值后 source .env 再启动。
"""
import os
import hashlib

# ── 身份 ─────────────────────────────────────────────────────────
# 你的微信 wxid（登录后在 DB 路径中可以看到，如 magicxinjx_c092 里的 magicxinjx）
MY_WXID = os.environ.get("WECHAT_MY_WXID", "your_wxid_here")

# ── 监听的对话 ───────────────────────────────────────────────────
# 填群 ID（xxxxx@chatroom）或个人 wxid，逗号分隔
# 例如：export WECHAT_WATCH_IDS="12345678@chatroom,wxid_xxxxxxxx"
_watch_env = os.environ.get("WECHAT_WATCH_IDS", "")
WATCH_IDS = [w.strip() for w in _watch_env.split(",") if w.strip()] if _watch_env else []
WATCH_TABLES = ["Msg_" + hashlib.md5(wid.encode()).hexdigest() for wid in WATCH_IDS]

# ── 触发词 ───────────────────────────────────────────────────────
# 其他人发消息含这些词时触发；自己只能用 /xin 触发
BOT_TRIGGERS = os.environ.get("BOT_TRIGGERS", "小昕,/xin").split(",")

# ── AI ───────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL      = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "1500"))
REPLY_PREFIX  = os.environ.get("REPLY_PREFIX", "👾 ")

AI_SYSTEM_PROMPT = (
    "你是AI助手。"
    "你能看到最近50条对话历史，充分利用上下文回答问题。"
    "如果被问到某人说了什么，直接从历史中找并总结，不要说看不到记录。"
    "仔细观察对话历史中用户本人的说话风格：用词、句子长短、语气、中英混用程度，模仿这个风格回复。"
    "根据上下文判断场合，不要把自己定义成某个特定群的助手。"
    "严禁：引导性emoji（😏😊✨🎯等）；套话（很棒/太好了/我理解）；结尾反问（有啥想说的吗/需要帮忙吗）；warm收尾（就这么多啦/希望有帮助等）；bullet point结构化。"
    "普通回复不换行，几句话连着写完。只有需要列举多个点时才换行。"
    "说完就结束，绝对不加任何收尾句。回复不要以" + REPLY_PREFIX.strip() + "开头。"
)

# ── 路径（根据你的微信账号自动生成，也可通过环境变量覆盖）──────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE     = os.path.join(BASE_DIR, "keys", "wechat_keys.json")
DB_DIR        = os.path.join(BASE_DIR, "db")
DECRYPTED_DB  = os.path.join(DB_DIR, "message_0.db")
SQLCIPHER_BIN = os.environ.get("SQLCIPHER_BIN", "/opt/homebrew/opt/sqlcipher/bin/sqlcipher")

# 微信数据库路径（路径中包含你的 wxid，需要替换）
# 格式：~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>_xxxx/db_storage/message/message_0.db
WECHAT_DB_PATH = os.environ.get(
    "WECHAT_DB_PATH",
    os.path.expanduser(f"~/Library/Containers/com.tencent.xinWeChat/Data/Documents"
                       f"/xwechat_files/{MY_WXID}_c092/db_storage/message/message_0.db")
)
WECHAT_WAL_PATH = WECHAT_DB_PATH + "-wal"

# ── 其他 ─────────────────────────────────────────────────────────
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "50"))
