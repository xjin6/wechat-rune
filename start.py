#!/usr/bin/env python3.9
"""
微信AI机器人启动脚本

用法：
    python3.9 start.py                   # 用 .watch 里的对话列表启动
    python3.9 start.py "SSCI Team" HK    # 临时指定对话名称
"""

import os, sys, subprocess, json, hashlib, glob, sqlite3

# ── 自动检测 wxid 和 DB 路径 ─────────────────────────────────────

def detect_wxid_and_db():
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    # 找 *_c092 风格的目录
    pattern = os.path.join(base, "*_c092", "db_storage", "message", "message_0.db")
    matches = glob.glob(pattern)
    if not matches:
        # 也试试其他后缀
        matches = glob.glob(os.path.join(base, "*", "db_storage", "message", "message_0.db"))
    if not matches:
        print("❌ 找不到微信数据库，请确认微信已登录")
        sys.exit(1)
    db_path = matches[0]
    # 从路径中提取 wxid
    wxid_part = db_path.split("/xwechat_files/")[1].split("/")[0]
    wxid = wxid_part.split("_")[0] if "_" in wxid_part else wxid_part
    return wxid, db_path


# ── 从 contact.db/session.db 查询对话 ID ─────────────────────────

def find_chat_id(name: str, db_path: str, keys: dict) -> str:
    """按名称（昵称/备注/群名）查找对话 ID，返回 wxid 或 chatroom ID"""
    sqlcipher = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"

    # 查 contact.db（联系人 + 群）
    contact_db = db_path.replace("/message/message_0.db", "/contact/contact.db")
    key = next((v for k, v in keys.items() if "contact/contact.db" in k), "")
    if key and os.path.exists(contact_db):
        sql_file = "/tmp/find_chat.sql"
        with open(sql_file, "w") as f:
            f.write('PRAGMA key = "x\'%s\'";\n' % key)
            f.write('PRAGMA cipher_page_size = 4096;\n')
            f.write('.separator "|||"\n')
            f.write(f"SELECT username, nick_name, remark FROM contact "
                    f"WHERE nick_name LIKE '%{name}%' OR remark LIKE '%{name}%' LIMIT 5;\n")
        r = subprocess.run([sqlcipher, contact_db], stdin=open(sql_file),
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "|||" in line:
                parts = line.split("|||")
                wxid, nick, remark = parts[0], parts[1], parts[2]
                display = remark or nick
                print(f"  ✓ 找到：{display}（{wxid}）")
                return wxid

    print(f"  ❌ 找不到「{name}」，请检查名称是否正确")
    return None


# ── 读取 API Key ────────────────────────────────────────────────

def get_api_key():
    # 1. 环境变量
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    # 2. 本地 .apikey 文件
    keyfile = os.path.join(os.path.dirname(__file__), ".apikey")
    if os.path.exists(keyfile):
        return open(keyfile).read().strip()
    print("❌ 缺少 ANTHROPIC_API_KEY，请运行：")
    print("   echo 'sk-ant-...' > .apikey")
    sys.exit(1)


# ── 读取默认监听列表 ─────────────────────────────────────────────

def get_watch_list(args, wxid, db_path, keys):
    """从命令行参数或 .watch 文件获取要监听的对话列表，返回 [wxid/chatroom_id, ...]"""
    names = list(args)

    if not names:
        watchfile = os.path.join(os.path.dirname(__file__), ".watch")
        if os.path.exists(watchfile):
            names = [l.strip() for l in open(watchfile) if l.strip() and not l.startswith("#")]
        else:
            print("❌ 没有指定对话，请：")
            print("   python3.9 start.py \"群名或联系人名\"")
            print("   或创建 .watch 文件，每行一个名称")
            sys.exit(1)

    watch_ids = []
    for name in names:
        # 已经是 ID 格式（含 @ 或 wxid_）
        if "@chatroom" in name or name.startswith("wxid_"):
            watch_ids.append(name)
            print(f"  ✓ {name}")
        else:
            chat_id = find_chat_id(name, db_path, keys)
            if chat_id:
                watch_ids.append(chat_id)

    return watch_ids


# ── 主流程 ───────────────────────────────────────────────────────

def main():
    print("🤖 微信AI机器人启动中...\n")

    # 加载解密 key
    keys_file = os.path.join(os.path.dirname(__file__), "keys", "wechat_keys.json")
    if not os.path.exists(keys_file):
        print("❌ 找不到 keys/wechat_keys.json，请先提取解密 key")
        sys.exit(1)
    keys = json.load(open(keys_file))

    # 自动检测
    wxid, db_path = detect_wxid_and_db()
    print(f"✓ 账号：{wxid}")
    print(f"✓ DB：{db_path}\n")

    # 获取监听列表
    print("📋 解析对话列表...")
    watch_ids = get_watch_list(sys.argv[1:], wxid, db_path, keys)

    if not watch_ids:
        print("❌ 没有有效的对话")
        sys.exit(1)

    api_key = get_api_key()

    print(f"\n✅ 准备监听 {len(watch_ids)} 个对话，启动中...\n")

    # 启动 bot
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key
    env["WECHAT_MY_WXID"] = wxid
    env["WECHAT_DB_PATH"] = db_path
    env["WECHAT_WATCH_IDS"] = ",".join(watch_ids)

    os.execve(sys.executable, [sys.executable, "-u", "bot.py"], env)


if __name__ == "__main__":
    main()
