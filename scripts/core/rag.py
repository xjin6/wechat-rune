"""
RAG retrieval module - unified entry point

Signal extraction -> entity expansion (alias resolution) -> routed search -> add context -> return extra_context
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
    Use Claude to analyze conversation history:
    1. Find aliases for names/terms in the query (e.g. 彭泰权 = winson)
    2. Combine previous conversation context to extract the real search terms for this query
    Returns (aliases, context_terms)
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
        "Recent conversation log:\n" + history_text + "\n\n"
        "Current user query: " + intent + "\n\n"
        "Complete two tasks, return only JSON:\n"
        '{"aliases": ["name aliases including the original"], "context_terms": ["key search terms derived from prior conversation context"]}\n\n'
        "Example: if the previous round discussed a meeting with winson/彭泰权, and the current query is 'when is the meeting', "
        "context_terms should include winson and meeting-related terms. Return empty arrays if none apply."
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
                print(f"[RAG-entity] aliases:{aliases} context_terms:{ctx_terms}", flush=True)
            return aliases, ctx_terms
    except Exception as e:
        print(f"[RAG-entity] Expansion failed: {e}", flush=True)
    return [], []

_member_cache: dict[str, set] = {}


def _extract_person(intent: str):
    """Extract a person's name from intent text, return (wxid_or_None, keyword_or_None, is_found)"""
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
    Check whether person_wxid is a member of the chat_wxid conversation.
    Group chat: query the chatroom_member table
    Private chat: the other party is chat_wxid itself
    """
    if not person_wxid or not chat_wxid:
        return False

    # Private chat: direct comparison
    if "@chatroom" not in chat_wxid:
        return person_wxid == chat_wxid

    # Group chat: check cache, load from contact.db if missing
    if chat_wxid not in _member_cache:
        _member_cache[chat_wxid] = _load_group_members(chat_wxid)

    return person_wxid in _member_cache[chat_wxid]


def _load_group_members(chatroom_wxid: str) -> set:
    """Load group member wxids from the chatroom_member table in contact.db"""
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
        # room_id corresponds to contact.id; first find the group's id
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
    Determine whether the query is ABOUT a person (they were mentioned) or FROM a person (what they said).
    Action verbs like "said/mentioned/chatted" immediately following the person's name -> FROM
    Otherwise -> ABOUT
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
    return True  # ABOUT (default: find all content about them)


def _format_msg(m: tuple, my_wxid_val: str, mark: bool = False) -> str:
    t = _dt.datetime.fromtimestamp(m[1]).strftime("%m-%d %H:%M")
    wx = _extract_sender_wxid(m[3])
    name = contact_cache.get(wx, "me" if m[2] == 1 else "?")
    text = extract_text(m[3])
    suffix = " ◀" if mark else ""
    return f"[{t}][{name}]: {text}{suffix}"


# ── Main function ─────────────────────────────────────────────────

def retrieve(trigger_text: str, table: str, my_wxid: str,
             history: list = None, client=None, chat_wxid: str = None) -> str:
    """
    Automatically route search based on query intent, return the assembled extra_context string.
    """
    intent = trigger_text
    for p in ["/xin ", "小昕", "@小昕"]:
        if intent.startswith(p):
            intent = intent[len(p):].strip()
            break

    # Step 1: Entity alias expansion (only runs when history and client are available)
    aliases, ctx_terms = [], []
    if history and client:
        aliases, ctx_terms = _expand_entities(intent, history, client)

    # Step 2: Extract signals
    date_range = parse_date_range(trigger_text)          # specific date: "4月1号"
    time_days = next((v for k, v in TIME_RANGES.items() if k in intent), None)
    person_wxid, person_kw, _ = _extract_person(intent)

    # Key rule: is this person a member of the current conversation?
    # Member -> FROM filter (find what they said)
    # Non-member -> third-party mention -> keyword/semantic search
    in_chat = _is_chat_member(person_wxid, chat_wxid or "") if person_wxid else False
    is_about = (not in_chat) or _is_about_person(intent, person_wxid)

    # Supplement person_kw with aliases
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

    # Path A: Specific date
    if date_range:
        label = _dt.datetime.fromtimestamp(date_range[0]).strftime("Messages from %m-%d")
        found = search_messages(
            table,
            person_wxid if person_wxid and not is_about else None,
            person_kw if not person_wxid else None,
            date_range=date_range, limit=50
        )
        add_msgs(found, f"[{label}]", with_context=False)
        print(f"[RAG-A] Date query: {len(found)} results", flush=True)

    # Path B: Time range
    elif time_days:
        found = search_messages(
            table,
            person_wxid if person_wxid and not is_about else None,
            person_kw if not person_wxid else None,
            days=time_days, limit=30
        )
        add_msgs(found, f"[Related messages from the last {time_days} days]", with_context=False)
        print(f"[RAG-B] Time range: {len(found)} results", flush=True)

    # Path D: Keyword search
    # Rule: person not in current chat OR no contact match -> do keyword search
    search_kws = list(dict.fromkeys(aliases + ctx_terms + ([person_kw] if person_kw else [])))
    if search_kws and (not in_chat or not person_wxid):
        all_kw_found = []
        for kw in search_kws:
            kw_found = search_messages(table, None, kw, days=365, limit=15)
            for m in kw_found:
                if m[0] not in seen_ids:
                    all_kw_found.append(m)
        if all_kw_found:
            add_msgs(all_kw_found, f'[Messages containing "{"/".join(search_kws)}"]', with_context=True)
        print(f"[RAG-D] Keywords {search_kws}: {len(all_kw_found)} results", flush=True)

    # Path C: Semantic search (always runs when vector store is available)
    vec_count = count(table)
    if vec_count > 0:
        sem_query = intent + " " + " ".join(ctx_terms) if ctx_terms else intent
        sem_all = semantic_search(table, sem_query, top_k=20)
        sem_all = [r for r in sem_all if r["score"] > 0.45]
        # FROM mode: keep only messages from this person
        if person_wxid and not is_about:
            name_parts = re.split(r'[（(）)]', contact_cache.get(person_wxid, ""))
            sem_all = [r for r in sem_all if any(p and p in r["sender"] for p in name_parts)]
        sem_new = [r for r in sem_all if r["local_id"] not in seen_ids]
        if sem_new:
            lines = ["[Semantically related historical messages]"]
            for r in sem_new:
                if r["local_id"] in seen_ids:
                    continue
                ctx = fetch_context(table, r["local_id"], window=4)
                for c in ctx:
                    if c[0] not in seen_ids:
                        seen_ids.add(c[0])
                        lines.append(_format_msg(c, my_wxid, mark=(c[0] == r["local_id"])))
            sections.append("\n".join(lines))
        print(f"[RAG-C] Semantic: {len(sem_new)} results (store: {vec_count})", flush=True)

    return "\n\n".join(sections)
