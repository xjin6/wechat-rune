"""
RAG 检索模块 - 统一入口

信号提取 → 实体扩展（别名解析）→ 路由搜索 → 加上下文 → 返回 extra_context
"""

import re, json
import datetime as _dt
from core.search import search_messages, parse_date_range, fetch_context, TIME_RANGES
from core.contacts import find_wxid, _cache as contact_cache
from core.embeddings import semantic_search, count
from core.reader import extract_text
from core.ai import _extract_sender_wxid


def _expand_entities(intent: str, history: list, client) -> tuple:
    """
    用 Claude 从对话历史中：
    1. 找出查询里人名/词语的别称（如 彭泰权 = winson）
    2. 结合上一轮对话语境，提取这次查询的真实搜索词
    返回 (aliases, context_terms)
    """
    if not history:
        return [], []

    lines = []
    for m in history[-30:]:
        text = extract_text(m[3])
        if text and not text.startswith("<"):
            lines.append(text[:100])
    if not lines:
        return [], []

    history_text = "\n".join(lines)
    prompt = (
        "最近对话记录：\n" + history_text + "\n\n"
        "用户当前查询：" + intent + "\n\n"
        "请完成两件事，只返回JSON：\n"
        '{"aliases": ["人名别称含原词"], "context_terms": ["结合上轮语境的关键搜索词"]}\n\n'
        "例如：上轮聊winson/彭泰权会议，这轮问'什么时候开会'，"
        "context_terms应包含winson和开会相关词。没有则返回空数组。"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{.*?\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            aliases = [str(a) for a in data.get("aliases", []) if a]
            ctx_terms = [str(a) for a in data.get("context_terms", []) if a]
            if aliases or ctx_terms:
                print(f"[RAG-entity] 别名:{aliases} 上下文词:{ctx_terms}", flush=True)
            return aliases, ctx_terms
    except Exception as e:
        print(f"[RAG-entity] 扩展失败: {e}", flush=True)
    return [], []

_member_cache: dict[str, set] = {}


def _extract_person(intent: str):
    """从意图文本中提取人名，返回 (wxid_or_None, keyword_or_None, is_found)"""
    for length in range(2, 7):
        for i in range(len(intent) - length + 1):
            substr = intent[i:i+length]
            if not re.search(r'[\u4e00-\u9fff\w]', substr):
                continue
            wxid = find_wxid(substr)
            if wxid:
                return wxid, None, True
    kw_text = intent
    for stop in ["有任何", "有没有", "有无", "关于", "的记录", "的事情", "的消息",
                 "说了", "说过", "提到", "聊过", "吗", "呢", "啊", "？", "?"]:
        kw_text = kw_text.replace(stop, " ")
    parts = [w for w in kw_text.split() if 1 < len(w) <= 6]
    return None, parts[0] if parts else None, False


def _is_chat_member(person_wxid: str, chat_wxid: str) -> bool:
    """
    判断 person_wxid 是否是 chat_wxid 这个对话的成员。
    群聊：查 chatroom_member 表
    私聊：对方就是 chat_wxid 本身
    """
    if not person_wxid or not chat_wxid:
        return False

    # 私聊：直接比较
    if "@chatroom" not in chat_wxid:
        return person_wxid == chat_wxid

    # 群聊：查缓存，没有则从 contact.db 加载
    if chat_wxid not in _member_cache:
        _member_cache[chat_wxid] = _load_group_members(chat_wxid)

    return person_wxid in _member_cache[chat_wxid]


def _load_group_members(chatroom_wxid: str) -> set:
    """从 contact.db 的 chatroom_member 表加载群成员 wxid"""
    import subprocess, json
    from config import KEYS_FILE, SQLCIPHER_BIN, WECHAT_DB_PATH

    contact_db = WECHAT_DB_PATH.replace("/message/message_0.db", "/contact/contact.db")
    key = next((v for k, v in json.load(open(KEYS_FILE)).items()
                if "contact/contact.db" in k), "")
    if not key:
        return set()

    with open('/tmp/members_q.sql', 'w') as f:
        f.write('PRAGMA key = "x\'%s\'";\n' % key)
        f.write('PRAGMA cipher_page_size = 4096;\n')
        f.write('.separator "|||"\n')
        # room_id 对应 contact.id，先找群的 id
        f.write(f"SELECT cm.member_id, c.username FROM chatroom_member cm "
                f"JOIN contact c2 ON c2.id = cm.room_id AND c2.username = '{chatroom_wxid}' "
                f"JOIN contact c ON c.id = cm.member_id;\n")

    r = subprocess.run(
        [SQLCIPHER_BIN, contact_db],
        stdin=open('/tmp/members_q.sql'),
        capture_output=True, text=True, timeout=5
    )
    members = set()
    for line in r.stdout.splitlines():
        if '|||' in line:
            parts = line.split('|||')
            if len(parts) >= 2 and parts[1].strip():
                members.add(parts[1].strip())
    return members


def _is_about_person(intent: str, person_wxid: str) -> bool:
    """
    判断查询是 ABOUT 某人（关于他/她被提及）还是 FROM 某人（他/她说了什么）
    有"说了/提到/聊过"等动作动词紧跟在人名后 → FROM
    否则 → ABOUT
    """
    action_verbs = ["说", "提", "聊", "讲", "谈", "发", "分享"]
    if person_wxid and person_wxid in contact_cache:
        name_parts = re.split(r'[（(）)]', contact_cache[person_wxid])
        for part in name_parts:
            if not part:
                continue
            idx = intent.find(part)
            if idx >= 0:
                after = intent[idx + len(part):]
                if any(after.startswith(v) for v in action_verbs):
                    return False  # FROM
    return True  # ABOUT（默认：找关于他的所有内容）


def _format_msg(m: tuple, my_wxid_val: str, mark: bool = False) -> str:
    t = _dt.datetime.fromtimestamp(m[1]).strftime("%m-%d %H:%M")
    wx = _extract_sender_wxid(m[3])
    name = contact_cache.get(wx, "我" if m[2] == 1 else "?")
    text = extract_text(m[3])
    suffix = " ◀" if mark else ""
    return f"[{t}][{name}]: {text}{suffix}"


# ── 主函数 ────────────────────────────────────────────────────────

def retrieve(trigger_text: str, table: str, my_wxid: str,
             history: list = None, client=None, chat_wxid: str = None) -> str:
    """
    根据查询意图自动路由搜索，返回拼好的 extra_context 字符串。
    """
    intent = trigger_text
    for p in ["/xin ", "小昕", "@小昕"]:
        if intent.startswith(p):
            intent = intent[len(p):].strip()
            break

    # ① 实体别名扩展（有 history 和 client 时才跑）
    aliases, ctx_terms = [], []
    if history and client:
        aliases, ctx_terms = _expand_entities(intent, history, client)

    # ② 提取信号
    date_range = parse_date_range(trigger_text)          # 具体日期："4月1号"
    time_days = next((v for k, v in TIME_RANGES.items() if k in intent), None)
    person_wxid, person_kw, _ = _extract_person(intent)

    # 关键规则：这个人是不是当前对话的成员？
    # 是成员 → FROM 过滤（找他说的）
    # 不是成员 → 第三方提及 → 关键词/语义搜索
    in_chat = _is_chat_member(person_wxid, chat_wxid or "") if person_wxid else False
    is_about = (not in_chat) or _is_about_person(intent, person_wxid)

    # 用别名补充 person_kw
    if aliases and not person_wxid:
        person_kw = person_kw or aliases[0]

    sections = []
    seen_ids = set()

    def add_msgs(msgs, label, with_context=False):
        if not msgs:
            return
        lines = [label]
        for m in msgs:
            if m[0] in seen_ids:
                continue
            if with_context:
                ctx = fetch_context(table, m[0], window=4)
                for c in ctx:
                    if c[0] not in seen_ids:
                        seen_ids.add(c[0])
                        lines.append(_format_msg(c, my_wxid, mark=(c[0] == m[0])))
            else:
                seen_ids.add(m[0])
                lines.append(_format_msg(m, my_wxid))
        sections.append("\n".join(lines))

    # ② 路径 A：具体日期
    if date_range:
        label = _dt.datetime.fromtimestamp(date_range[0]).strftime("%m月%d日的消息")
        found = search_messages(
            table,
            person_wxid if person_wxid and not is_about else None,
            person_kw if not person_wxid else None,
            date_range=date_range, limit=50
        )
        add_msgs(found, f"[{label}]", with_context=False)
        print(f"[RAG-A] 日期查询{len(found)}条", flush=True)

    # ③ 路径 B：时间范围
    elif time_days:
        found = search_messages(
            table,
            person_wxid if person_wxid and not is_about else None,
            person_kw if not person_wxid else None,
            days=time_days, limit=30
        )
        add_msgs(found, f"[最近{time_days}天相关消息]", with_context=False)
        print(f"[RAG-B] 时间范围{len(found)}条", flush=True)

    # ④ 路径 D：关键词搜索
    # 规则：此人不在当前对话 OR 没有匹配到联系人 → 做关键词搜索
    search_kws = list(dict.fromkeys(aliases + ctx_terms + ([person_kw] if person_kw else [])))
    if search_kws and (not in_chat or not person_wxid):
        all_kw_found = []
        for kw in search_kws:
            kw_found = search_messages(table, None, kw, days=365, limit=15)
            for m in kw_found:
                if m[0] not in seen_ids:
                    all_kw_found.append(m)
        if all_kw_found:
            add_msgs(all_kw_found, f'[含"{"/".join(search_kws)}"的消息]', with_context=True)
        print(f"[RAG-D] 关键词{search_kws}找到{len(all_kw_found)}条", flush=True)

    # ⑤ 路径 C：语义搜索（有向量库时必跑）
    vec_count = count(table)
    if vec_count > 0:
        sem_query = intent + " " + " ".join(ctx_terms) if ctx_terms else intent
        sem_all = semantic_search(table, sem_query, top_k=20)
        sem_all = [r for r in sem_all if r["score"] > 0.45]
        # FROM 模式：只保留该人的消息
        if person_wxid and not is_about:
            name_parts = re.split(r'[（(）)]', contact_cache.get(person_wxid, ""))
            sem_all = [r for r in sem_all if any(p and p in r["sender"] for p in name_parts)]
        sem_new = [r for r in sem_all if r["local_id"] not in seen_ids]
        if sem_new:
            lines = ["[语义相关历史消息]"]
            for r in sem_new:
                if r["local_id"] in seen_ids:
                    continue
                ctx = fetch_context(table, r["local_id"], window=4)
                for c in ctx:
                    if c[0] not in seen_ids:
                        seen_ids.add(c[0])
                        lines.append(_format_msg(c, my_wxid, mark=(c[0] == r["local_id"])))
            sections.append("\n".join(lines))
        print(f"[RAG-C] 语义{len(sem_new)}条(库{vec_count})", flush=True)

    return "\n\n".join(sections)
