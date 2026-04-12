"""
微信AI机器人配置
修改这里来定制机器人行为
"""
import os
import hashlib

# ── 身份 ─────────────────────────────────────────────────────────
MY_WXID = "magicxinjx"

# ── 监听的对话（群ID 或 个人wxid）────────────────────────────────
# 添加更多对话只需在这里加一行
WATCH_IDS = [
    "34422179829@chatroom",   # SSCI Team群
    "wxid_iq08s7oagntq12",    # 杨晨
    "wxid_iv139ys0vn3412",    # HK
]
WATCH_TABLES = ["Msg_" + hashlib.md5(wid.encode()).hexdigest() for wid in WATCH_IDS]

# ── 触发词 ───────────────────────────────────────────────────────
BOT_TRIGGERS = ["小昕", "/xin"]

# ── AI ───────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL = "claude-haiku-4-5-20251001"
AI_MAX_TOKENS = 1500  # 最大回复长度，haiku上限8192
AI_SYSTEM_PROMPT = (
    "你是AI助手小昕。"
    "你能看到最近50条对话历史，充分利用上下文回答问题。"
    "如果被问到某人说了什么，直接从历史中找并总结，不要说看不到记录。"
    "仔细观察对话历史中用户本人的说话风格：用词、句子长短、语气、中英混用程度，模仿这个风格回复。"
    "根据上下文判断场合，不要把自己定义成某个特定群的助手。"
    "严禁：引导性emoji（😏😊✨🎯等）；套话（很棒/太好了/我理解）；结尾反问（有啥想说的吗/需要帮忙吗）；warm收尾（就这么多啦/希望有帮助等）；bullet point结构化。"
    "普通回复不换行，几句话连着写完。只有需要列举多个点时才换行。"
    "说完就结束，绝对不加任何收尾句，例如'就这样了''怎么了''有什么不明白吗'这类全不要。回复不要以👾开头。"
)
REPLY_PREFIX = "👾 "

# ── 路径 ─────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(BASE_DIR, "keys", "wechat_keys.json")
DB_DIR    = os.path.join(BASE_DIR, "db")
DECRYPTED_DB = os.path.join(DB_DIR, "message_0.db")

WECHAT_DB_PATH = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents"
    "/xwechat_files/magicxinjx_c092/db_storage/message/message_0.db"
)
# 监听WAL文件而非主DB文件，消息写入后立即触发
WECHAT_WAL_PATH = WECHAT_DB_PATH + "-wal"
SQLCIPHER_BIN = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"

# ── 其他 ─────────────────────────────────────────────────────────
POLL_INTERVAL = 0.5  # 轮询间隔（秒）
MAX_HISTORY   = 50  # 传给AI的历史消息数
