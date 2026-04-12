"""Claude AI 回复生成"""
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


def generate(history: list, trigger_text: str, **kwargs) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = AI_SYSTEM_PROMPT

    from config import REPLY_PREFIX, MY_WXID

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

    resp = client.messages.create(
        model=AI_MODEL, max_tokens=AI_MAX_TOKENS,
        system=system, messages=msgs
    )
    return resp.content[0].text if resp.content else "（回复生成失败，请重试）"
