"""Claude AI reply generation (with RAG history search)"""
import json, re, time
import anthropic
from config import ANTHROPIC_API_KEY, AI_MODEL, AI_SYSTEM_PROMPT, AI_MAX_TOKENS
from core.contacts import get_name
from core.reader import extract_text


def _extract_sender_wxid(hex_content: str) -> str:
    """Extract sender wxid from the first line of a group message"""
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
    r"\d+月\d+[号日]", r"\d+[/-]\d+",          # specific dates
    r"有(没有|无|过)(说|提|聊|讲|谈|讨论)",    # "have you said" / "has anyone mentioned"
    r"(曾经|曾|是否|有否)(说|提|聊|讲|谈)",    # "ever said" / "whether mentioned"
    r"(说过|提过|聊过|讨论过|谈过)",             # "said before" / "mentioned before"
    r"任何.*关于", r"关于",                      # "about xxx"
    r".+是谁", r".+是什么", r".+怎么了",        # follow-up questions: who is / what is
    r"(他|她|它|那个|这个).*(是|在|做|说|去)",  # pronoun follow-ups
    r"(安排|计划|打算|约|决定|定了)",            # arrangements / plans
    r"(什么时候|啥时候|几号|几点|哪天)",          # time queries
    r"(怎么说|说啥|说什么|聊什么|讲什么)",        # content queries
    r"(后来|后面|然后|最后|结果)",                # follow-up inquiries
]

def _needs_search_heuristic(text: str) -> bool:
    """Quick heuristic check whether a history search may be needed"""
    return any(re.search(p, text) for p in _NEEDS_SEARCH_RULES)


def _classify_search(text: str, client: anthropic.Anthropic) -> dict:
    """Ask Claude whether a history search is needed; return search params"""
    # Strip trigger-word prefix
    clean = text
    for prefix in ["/xin ", "小昕", "@小昕"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
            break

    prompt = (
        f'User asked: "{clean}"\n\n'
        'This is a WeChat chat history query system. Determine whether this question requires searching historical messages to answer.\n'
        'The following situations require a search: asking what someone said/mentioned/discussed, or containing time words like last week/before/that time/history.\n'
        'Return only JSON, no other text:\n'
        'Needs search: {"search": true, "person": "name (if any) or null", "keyword": "keyword (if any) or null", "time": "time description (e.g. last week) or null"}\n'
        'No search needed: {"search": false}'
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

    # ── RAG ─────────────────────────────────────────────────────
    extra_context = ""
    needs_rag = table and _needs_search_heuristic(trigger_text)
    print(f"[RAG] heuristic={needs_rag} | text={trigger_text[:60]}", flush=True)
    if needs_rag:
        from core.rag import retrieve
        extra_context = retrieve(trigger_text, table, MY_WXID, history=history, client=client, chat_wxid=kwargs.get("chat_wxid"))
        print(f"[RAG] context={len(extra_context)} chars", flush=True)

    # Build conversation history with sender names
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
                name = "me"
            else:
                name = "other"
            content = f"[{name}]: {text}"
        else:
            content = text
        raw.append({"role": role, "content": content})

    # Merge consecutive messages from the same role
    msgs = []
    for m in raw:
        if msgs and msgs[-1]["role"] == m["role"]:
            msgs[-1]["content"] += "\n" + m["content"]
        else:
            msgs.append(m)

    # Ensure the list starts and ends with a user message
    if msgs and msgs[0]["role"] == "assistant":
        msgs = msgs[1:]
    if not msgs or msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "content": trigger_text})
    if not msgs:
        msgs = [{"role": "user", "content": trigger_text}]

    # Append RAG search results to the last user message
    if extra_context:
        msgs[-1]["content"] = extra_context + "\n\n" + msgs[-1]["content"]

    total_chars = sum(len(m["content"]) for m in msgs)
    print(f"[Claude] {len(msgs)} msgs, {total_chars} chars to API", flush=True)

    resp = client.messages.create(
        model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
        system=system, messages=msgs
    )
    return resp.content[0].text if resp.content else "(Reply generation failed, please retry)"
