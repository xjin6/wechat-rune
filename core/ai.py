"""Claude AI 回复生成（含 RAG 历史搜索）"""
import json, re
import anthropic
from config import ANTHROPIC_API_KEY, AI_MODEL, AI_SYSTEM_PROMPT, AI_MAX_TOKENS
from core.contacts import get_name
from core.reader import extract_text


def _extract_sender_wxid(hex_content: str) -> str:
    """从群消息内容第一行提取发送者wxid"""
    import zstandard, re as _re
    try:
        raw = bytes.fromhex(hex_content) if hex_content else b''
    except ValueError:
        return ''
    if raw[:4] == b'\x28\xb5\x2f\xfd':
        try:
            text = zstandard.decompress(raw).decode('utf-8', errors='replace')
        except Exception:
            return ''
    else:
        text = raw.decode('utf-8', errors='replace')
    if '\n' in text:
        first = text.split('\n', 1)[0].strip()
        if _re.match(r'^[\w]{4,30}:$', first):
            return first.rstrip(':')
    return ''


_NEEDS_SEARCH_RULES = [
    r"上(周|个月|次|回|星期)", r"last\s*(week|month)", r"之前", r"刚才以外",
    r"(说了|提到|聊过|讨论过).*什么", r"历史", r"记录", r"那时候", r"当时",
    r"\d+月\d+[号日]",   # 具体日期：4月1号、3月15日
    r"\d+[/-]\d+",       # 4/1、4-1 格式
]

def _needs_search_heuristic(text: str) -> bool:
    """规则快速判断是否可能需要历史搜索"""
    return any(re.search(p, text) for p in _NEEDS_SEARCH_RULES)


def _classify_search(text: str, client: anthropic.Anthropic) -> dict:
    """让 Claude 判断是否需要历史搜索，返回搜索参数"""
    # 去掉触发词前缀
    clean = text
    for prefix in ["/xin ", "小昕", "@小昕"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
            break

    prompt = (
        f'用户问：「{clean}」\n\n'
        '这是一个微信聊天记录查询系统。判断这个问题是否需要从历史消息中搜索才能回答。\n'
        '以下情况必须搜索：问某人说了什么/提到了什么/聊了什么、含上周/之前/那次/历史等时间词。\n'
        '只返回JSON，不要其他文字：\n'
        '需要搜索：{"search": true, "person": "人名（如有）或null", "keyword": "关键词（如有）或null", "time": "时间描述（如上周）或null"}\n'
        '不需要：{"search": false}'
    )
    try:
        resp = client.messages.create(
            model=AI_MODEL, max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        text_resp = resp.content[0].text.strip()
        return json.loads(text_resp)
    except Exception:
        return {"search": False}


def generate(history: list, trigger_text: str,
             table: str = None, my_wxid: str = None, **kwargs) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = AI_SYSTEM_PROMPT

    from config import REPLY_PREFIX, MY_WXID
    my_wxid = my_wxid or MY_WXID

    # ── RAG：规则直接触发历史搜索 ────────────────────────────────
    extra_context = ""
    if table and _needs_search_heuristic(trigger_text):
        from core.search import search_messages, format_search_results, parse_days
        from core.contacts import _cache as contact_cache
        from core.search import TIME_RANGES

        # 去掉触发词前缀
        clean = trigger_text
        for prefix in ["/xin ", "小昕", "@小昕"]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
                break

        # 从文本里提取时间范围
        days = 30
        for k, v in TIME_RANGES.items():
            if k in clean:
                days = v
                break

        # 从文本里提取人名：试所有2-5字子串，找到联系人就停
        from core.contacts import find_wxid
        person_wxid = None
        for length in range(2, 6):
            if person_wxid:
                break
            for i in range(len(clean) - length + 1):
                substr = clean[i:i+length]
                if not re.search(r'[\u4e00-\u9fff\w]', substr):
                    continue
                wxid = find_wxid(substr)
                if wxid:
                    person_wxid = wxid
                    break

        # 提取关键词（去掉人名、时间词、助词后剩下的实词）
        keyword = None
        for stop in list(TIME_RANGES.keys()) + ["说了", "说过", "提到", "聊过", "讨论", "什么", "说的", "怎么说"]:
            clean = clean.replace(stop, " ")
        # 去掉已识别的人名
        if person_wxid and person_wxid in contact_cache:
            for part in re.split(r'[（(）)]', contact_cache[person_wxid]):
                clean = clean.replace(part, " ")
        keyword_parts = [w for w in clean.split() if len(w) > 1]
        if keyword_parts:
            keyword = keyword_parts[0]

        from core.search import parse_date_range
        date_range = parse_date_range(trigger_text)
        if date_range:
            found = search_messages(table, person_wxid, keyword, date_range=date_range)
        else:
            found = search_messages(table, person_wxid, keyword, days)
        if found:
            extra_context = format_search_results(found, my_wxid)
            print(f"[RAG] 找到 {len(found)} 条历史消息", flush=True)

    # 构建对话历史，带发送者名字
    raw = []
    for r in history:
        text = extract_text(r[3])
        if not text or text.startswith("<"):
            continue
        role = "assistant" if text.startswith(REPLY_PREFIX.strip()) else "user"
        if role == "user":
            sender_wxid = _extract_sender_wxid(r[3])
            if sender_wxid and sender_wxid != MY_WXID:
                name = get_name(sender_wxid)
            elif r[2] == 1:
                name = "我"
            else:
                name = "对方"
            content = f"[{name}]: {text}"
        else:
            content = text
        raw.append({"role": role, "content": content})

    # 合并连续相同角色
    msgs = []
    for m in raw:
        if msgs and msgs[-1]["role"] == m["role"]:
            msgs[-1]["content"] = m["content"]
        else:
            msgs.append(m)

    # 确保以 user 开头、user 结尾
    if msgs and msgs[0]["role"] == "assistant":
        msgs = msgs[1:]
    if not msgs or msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "content": trigger_text})
    if not msgs:
        msgs = [{"role": "user", "content": trigger_text}]

    # 把 RAG 搜索结果追加到最后一条 user 消息
    if extra_context:
        msgs[-1]["content"] = extra_context + "\n\n" + msgs[-1]["content"]

    resp = client.messages.create(
        model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
        system=system, messages=msgs
    )
    return resp.content[0].text if resp.content else "（回复生成失败，请重试）"
