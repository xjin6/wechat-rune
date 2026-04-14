"""
WeChat AI Bot Dashboard — 3-second polling
python3.9 dashboard.py → http://localhost:7788
"""
import os, sys, json, hashlib, sqlite3, subprocess, time, glob, tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

WATCH_FILE = os.path.join(PROJECT_ROOT, ".watch")
VECTOR_DB  = os.path.join(PROJECT_ROOT, "db", "vectors.db")
KEYS_FILE  = os.path.join(SCRIPT_DIR, "keys", "wechat_keys.json")
SQLCIPHER  = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"

def _detect_db_path():
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    matches = glob.glob(os.path.join(base, "*_c092", "db_storage", "message", "message_0.db"))
    if not matches:
        matches = glob.glob(os.path.join(base, "*", "db_storage", "message", "message_0.db"))
    return matches[0] if matches else None

_MSG_DB = _detect_db_path()


def _sqlcipher_query(db_path, key, sql_body):
    """Run a sqlcipher query and return rows."""
    fd, sql_file = tempfile.mkstemp(suffix='.sql', prefix='dq_')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(f'PRAGMA key="x\'{key}\'";\nPRAGMA cipher_page_size=4096;\n.separator "|||"\n')
            f.write(sql_body + '\n')
        r = subprocess.run([SQLCIPHER, db_path], stdin=open(sql_file),
                           capture_output=True, text=True, timeout=10)
    finally:
        os.unlink(sql_file)
    rows = []
    for line in r.stdout.splitlines():
        if not line or line == 'ok':
            continue
        rows.append(tuple(line.split('|||')))
    return rows


def _watched():
    if not os.path.exists(WATCH_FILE): return []
    return [l.strip() for l in open(WATCH_FILE) if l.strip() and not l.startswith("#")]


def _bot_on():
    r = subprocess.run(["pgrep", "-f", "python3.9 -u bot"], capture_output=True, text=True)
    pids = [p.strip() for p in r.stdout.strip().split() if p.strip()]
    return bool(pids), ", ".join(pids)


def _counts(wxid):
    table = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()
    total = 0
    if _MSG_DB:
        try:
            keys = json.load(open(KEYS_FILE))
            key = next((v for k, v in keys.items() if "message/message_0.db" in k), "")
            if key:
                t = _sqlcipher_query(_MSG_DB, key, f"SELECT COUNT(*) FROM {table} WHERE local_type=1;")
                total = int(t[0][0]) if t and t[0][0] else 0
        except Exception:
            pass
    try:
        conn = sqlite3.connect(VECTOR_DB)
        vec  = conn.execute("SELECT COUNT(*) FROM message_vectors WHERE table_name=?", (table,)).fetchone()[0]
        conn.close()
    except Exception:
        vec = 0
    need = max(0, total - 100)
    pct  = min(100, int(vec / need * 100)) if need > 0 else 100
    return total, vec, need, pct


def _resolve(name):
    if name.startswith("wxid_") or "@chatroom" in name:
        # Group chat: try to get group name from contact.db
        if "@chatroom" in name and _MSG_DB:
            try:
                keys = json.load(open(KEYS_FILE))
                key = next((v for k, v in keys.items() if "contact/contact.db" in k), "")
                if key:
                    db = _MSG_DB.replace("/message/message_0.db", "/contact/contact.db")
                    rows = _sqlcipher_query(db, key,
                        f"SELECT nick_name FROM contact WHERE username='{name}' LIMIT 1;")
                    if rows and rows[0][0]:
                        return name, rows[0][0]
            except Exception:
                pass
        return name, name
    try:
        keys = json.load(open(KEYS_FILE))
        key  = next((v for k,v in keys.items() if "contact/contact.db" in k), "")
        if not key or not _MSG_DB:
            return name, name
        db = _MSG_DB.replace("/message/message_0.db", "/contact/contact.db")
        sql = (f"SELECT username,remark,nick_name FROM contact WHERE (nick_name='{name}' OR remark='{name}') AND username NOT LIKE 'gh_%' LIMIT 1;\n"
               f"SELECT username,remark,nick_name FROM contact WHERE (nick_name LIKE '%{name}%' OR remark LIKE '%{name}%') AND username NOT LIKE 'gh_%' LIMIT 1;\n")
        rows = _sqlcipher_query(db, key, sql)
        for r in rows:
            if len(r) >= 3:
                return r[0].strip(), (r[1] or r[2] or name).strip()
    except Exception:
        pass
    return name, name


def get_data():
    bot, pids = _bot_on()
    convs = []
    for name in _watched():
        wxid, display = _resolve(name)
        total, vec, need, pct = _counts(wxid)
        convs.append({"id": wxid, "display": display,
                      "total": total, "vec": vec, "need": need, "pct": pct})
    return {"bot": bot, "pids": pids, "convs": convs}


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>WeChat AI Bot</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#f5f5f7;color:#1d1d1f;padding:32px 20px}
.wrap{max-width:660px;margin:0 auto}
h1{font-size:24px;font-weight:700;margin-bottom:20px}
.card{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.07)}
.status{display:flex;align-items:center;gap:10px}
.dot{width:9px;height:9px;border-radius:50%;transition:background .5s}
.st{font-size:15px;font-weight:500}
.sub{font-size:12px;color:#999;margin-top:2px}
.row{display:flex;align-items:center;gap:14px;padding:14px 0;border-bottom:.5px solid #f0f0f0}
.row:last-child{border:none}
.info{min-width:160px}
.name{font-size:15px;font-weight:500}
.nums{font-size:12px;color:#999;margin-top:3px}
.bar-wrap{flex:1}
.bar{height:6px;background:#e5e5ea;border-radius:3px;overflow:hidden}
.fill{height:100%;border-radius:3px;transition:width .6s ease}
.pct-txt{font-size:11px;color:#999;margin-top:4px}
.badge{font-size:12px;padding:3px 9px;border-radius:6px;font-weight:600;white-space:nowrap;transition:all .3s}
.ok{background:#d1fae5;color:#065f46}
.wip{background:#fef3c7;color:#92400e}
.live{font-size:11px;color:#bbb;text-align:right;margin-top:8px;display:flex;align-items:center;justify-content:flex-end;gap:5px}
.pulse{width:6px;height:6px;border-radius:50%;background:#30d158;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.8)}}
</style>
</head>
<body>
<div class="wrap">
<h1>👾 WeChat AI Bot</h1>

<div class="card">
  <div class="status">
    <div class="dot" id="dot"></div>
    <div>
      <div class="st" id="bot-st">Connecting...</div>
      <div class="sub" id="bot-sub">—</div>
    </div>
  </div>
</div>

<div class="card">
  <div style="font-weight:600;margin-bottom:12px">Watched Conversations (<span id="n">—</span>)</div>
  <div id="convs"></div>
</div>

<div class="live"><div class="pulse"></div>Live updating</div>
</div>

<script>
function update(d) {
  document.getElementById('dot').style.background = d.bot ? '#30d158' : '#ff3b30';
  document.getElementById('bot-st').textContent = d.bot ? 'Running' : 'Stopped';
  document.getElementById('bot-sub').textContent = d.bot ? 'PID: ' + d.pids : '—';
  document.getElementById('n').textContent = d.convs.length;

  const container = document.getElementById('convs');
  d.convs.forEach(c => {
    let row = document.getElementById('row-' + c.id);
    if (!row) {
      row = document.createElement('div');
      row.className = 'row';
      row.id = 'row-' + c.id;
      row.innerHTML = `
        <div class="info">
          <div class="name">${c.display}</div>
          <div class="nums" id="nums-${c.id}"></div>
        </div>
        <div class="bar-wrap">
          <div class="bar"><div class="fill" id="fill-${c.id}" style="width:0%"></div></div>
          <div class="pct-txt" id="pct-${c.id}"></div>
        </div>
        <span class="badge" id="badge-${c.id}"></span>`;
      container.appendChild(row);
    }
    const pct = c.pct;
    const done = pct >= 85;
    const color = done ? '#30d158' : (pct > 50 ? '#007aff' : '#ff9500');
    document.getElementById('fill-' + c.id).style.cssText = `width:${pct}%;background:${color}`;
    document.getElementById('nums-' + c.id).textContent = `${c.total} total · ${c.need} history`;
    document.getElementById('pct-' + c.id).textContent = `${Math.min(c.vec, c.need)}/${c.need} vectorized`;
    const badge = document.getElementById('badge-' + c.id);
    badge.textContent = done ? '✓ Done' : pct + '%';
    badge.className = 'badge ' + (done ? 'ok' : 'wip');
  });
}
function poll() {
  fetch('/api').then(r => r.json()).then(update).catch(() => {
    document.getElementById('bot-st').textContent = 'Dashboard disconnected';
  });
}
poll();
setInterval(poll, 3000);
</script>
</body>
</html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            data = json.dumps(get_data())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data.encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def log_message(self, *a): pass


if __name__ == "__main__":
    port = 7788
    print(f"Dashboard → http://localhost:{port}")
    HTTPServer(("", port), H).serve_forever()
