"""
微信AI机器人 Dashboard
用法：python3.9 dashboard.py
然后在浏览器打开 http://localhost:7788
"""

import os, sys, json, hashlib, sqlite3, subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WATCH_FILE  = os.path.join(os.path.dirname(__file__), ".watch")
VECTOR_DB   = os.path.join(os.path.dirname(__file__), "db", "vectors.db")
KEYS_FILE   = os.path.join(os.path.dirname(__file__), "keys", "wechat_keys.json")
SQLCIPHER   = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"


def get_status():
    # 读 .watch
    watch_names = []
    if os.path.exists(WATCH_FILE):
        for line in open(WATCH_FILE):
            l = line.strip()
            if l and not l.startswith("#"):
                watch_names.append(l)

    # bot 进程
    r = subprocess.run(["pgrep", "-f", "python3.9 -u bot"], capture_output=True, text=True)
    pids = [p.strip() for p in r.stdout.strip().split() if p.strip()]
    bot_running = len(pids) > 0

    # 解析每个监听对话
    conversations = []
    for name in watch_names:
        wxid = name if (name.startswith("wxid_") or "@chatroom" in name) else _resolve_name(name)
        if not wxid:
            conversations.append({"name": name, "wxid": "?", "display": name,
                                   "total": 0, "vectorized": 0, "error": "找不到"})
            continue

        table = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()
        display = _get_display_name(wxid) or name

        # 向量化数
        vec = 0
        if os.path.exists(VECTOR_DB):
            try:
                conn = sqlite3.connect(VECTOR_DB)
                row = conn.execute("SELECT COUNT(*) FROM message_vectors WHERE table_name=?", (table,)).fetchone()
                vec = row[0] if row else 0
                conn.close()
            except Exception:
                pass

        # 总消息数
        total = _count_messages(table)

        conversations.append({
            "name": name, "wxid": wxid, "display": display,
            "total": total, "vectorized": vec,
        })

    return {"bot_running": bot_running, "pids": pids,
            "conversations": conversations, "watch_file": WATCH_FILE}


def _resolve_name(name):
    """名字 → wxid"""
    keys = json.load(open(KEYS_FILE)) if os.path.exists(KEYS_FILE) else {}
    key = next((v for k, v in keys.items() if "contact/contact.db" in k), "")
    from config import WECHAT_DB_PATH, SQLCIPHER_BIN
    contact_db = WECHAT_DB_PATH.replace("/message/message_0.db", "/contact/contact.db")
    if not key or not os.path.exists(contact_db):
        return None
    with open("/tmp/dash_q.sql", "w") as f:
        f.write(f'PRAGMA key = "x\'{key}\'";\nPRAGMA cipher_page_size = 4096;\n.separator "|||"\n')
        f.write(f"SELECT username FROM contact WHERE (nick_name='{name}' OR remark='{name}') AND username NOT LIKE 'gh_%' LIMIT 1;\n")
        f.write(f"SELECT username FROM contact WHERE (nick_name LIKE '%{name}%' OR remark LIKE '%{name}%') AND username NOT LIKE 'gh_%' LIMIT 1;\n")
    r = subprocess.run([SQLCIPHER_BIN, contact_db], stdin=open("/tmp/dash_q.sql"),
                       capture_output=True, text=True, timeout=5)
    for line in r.stdout.splitlines():
        if line and line != "ok" and "|||" not in line:
            return line.strip()
        if "|||" in line:
            return line.split("|||")[0].strip()
    return None


def _get_display_name(wxid):
    try:
        from core.contacts import _cache
        return _cache.get(wxid, "")
    except Exception:
        return ""


def _count_messages(table):
    try:
        from core.decrypt import query
        rows = query(f"SELECT COUNT(*) FROM {table} WHERE local_type=1;")
        return int(rows[0][0]) if rows and rows[0][0] else 0
    except Exception:
        return 0


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="5">
<title>WeChat AI Bot</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; background: #f5f5f7; color: #1d1d1f; }}
  h1 {{ font-size: 28px; font-weight: 600; }}
  .card {{ background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  .status {{ display: flex; align-items: center; gap: 10px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .green {{ background: #30d158; }}
  .red {{ background: #ff3b30; }}
  .label {{ font-size: 13px; color: #666; margin-bottom: 4px; }}
  .value {{ font-size: 16px; font-weight: 500; }}
  .chat-row {{ display: flex; align-items: center; padding: 12px 0; border-bottom: 1px solid #f0f0f0; gap: 16px; }}
  .chat-row:last-child {{ border-bottom: none; }}
  .chat-name {{ flex: 1; font-weight: 500; }}
  .chat-sub {{ font-size: 12px; color: #999; margin-top: 2px; }}
  .progress-wrap {{ flex: 2; }}
  .progress-bar {{ height: 6px; background: #e5e5ea; border-radius: 3px; overflow: hidden; }}
  .progress-fill {{ height: 100%; background: #007aff; border-radius: 3px; transition: width .3s; }}
  .progress-text {{ font-size: 12px; color: #666; margin-top: 4px; }}
  .badge {{ font-size: 12px; padding: 3px 8px; border-radius: 6px; font-weight: 500; }}
  .badge-ok {{ background: #d1fae5; color: #065f46; }}
  .badge-wip {{ background: #fef3c7; color: #92400e; }}
  .refresh {{ font-size: 12px; color: #999; text-align: right; margin-top: 8px; }}
</style>
</head>
<body>
<h1>👾 WeChat AI Bot</h1>

<div class="card">
  <div class="status">
    <div class="dot {dot_class}"></div>
    <div>
      <div class="value">Bot {bot_status}</div>
      <div class="label">PID: {pids}</div>
    </div>
  </div>
</div>

<div class="card">
  <div style="font-weight:600;margin-bottom:12px;">监听中的对话 ({count})</div>
  {chat_rows}
</div>

<div class="refresh">每5秒自动刷新</div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = get_status()

        dot = "green" if status["bot_running"] else "red"
        bot_txt = "运行中" if status["bot_running"] else "已停止"
        pids_txt = ", ".join(status["pids"]) if status["pids"] else "—"

        rows = ""
        for c in status["conversations"]:
            total = c["total"]
            vec = c["vectorized"]
            deque_size = min(total, 100)
            need_vec = max(0, total - deque_size)
            pct = min(100, int(vec / need_vec * 100)) if need_vec > 0 else 100
            done = vec >= need_vec * 0.85  # 85%以上视为完成（部分消息因内容太短被跳过）
            badge = f'<span class="badge badge-ok">✓ 向量化完成</span>' if done else f'<span class="badge badge-wip">向量化中 {pct}%</span>'
            rows += f"""
            <div class="chat-row">
              <div>
                <div class="chat-name">{c["display"] or c["name"]}</div>
                <div class="chat-sub">{c["wxid"][:30]}</div>
              </div>
              <div class="progress-wrap">
                <div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div>
                <div class="progress-text">
                  共 {total} 条消息 · 最近100条在内存 · {min(vec, need_vec)}/{need_vec} 历史消息已向量化
                </div>
              </div>
              {badge}
            </div>"""

        html = HTML_TEMPLATE.format(
            dot_class=dot, bot_status=bot_txt, pids=pids_txt,
            count=len(status["conversations"]), chat_rows=rows
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, *args):
        pass  # 静默日志


if __name__ == "__main__":
    port = 7788
    print(f"Dashboard: http://localhost:{port}")
    HTTPServer(("", port), Handler).serve_forever()
