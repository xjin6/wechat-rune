"""历史消息搜索——用于 RAG 动态扩展 context"""
import time
from core.decrypt import query
from core.reader import extract_text, decode_raw
from core.contacts import get_name


# ── 时间范围解析 ──────────────────────────────────────────────────

TIME_RANGES = {
    "今天": 1, "today": 1,
    "昨天": 2, "yesterday": 2,
    "上周": 7, "last week": 7, "这周": 7,
    "最近": 14, "recent": 14,
    "上个月": 30, "last month": 30,
    "最近一个月": 30,
}

def parse_days(time_str: str) -> int:
    """把时间描述转成天数，找不到返回 30"""
    if not time_str:
        return 30
    for k, v in TIME_RANGES.items():
        if k in time_str.lower():
            return v
    return 30


def parse_date_range(text: str):
    """从文本里提取具体日期，返回 (start_ts, end_ts) 或 None"""
    import re, datetime
    now = datetime.datetime.now()

    # 匹配 "4月1号" / "4月1日"
    m = re.search(r'(\d+)月(\d+)[号日]', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year if month <= now.month else now.year - 1
        try:
            dt = datetime.datetime(year, month, day)
            start = int(dt.timestamp())
            end = start + 86400  # 当天
            return start, end
        except ValueError:
            pass

    # 匹配 "4/1" / "4-1"
    m = re.search(r'(\d{1,2})[/-](\d{1,2})', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year if month <= now.month else now.year - 1
        try:
            dt = datetime.datetime(year, month, day)
            start = int(dt.timestamp())
            end = start + 86400
            return start, end
        except ValueError:
            pass

    return None


# ── 核心搜索 ──────────────────────────────────────────────────────

def search_messages(table: str, person_wxid: str = None,
                    keyword: str = None, days: int = 30,
                    limit: int = 20, date_range=None) -> list[tuple]:
    """
    在指定对话表里搜索历史消息。
    返回格式与 deque 里的消息元组相同：
    (local_id, create_time, real_sender_id, hex_content, hex_source)
    """
    if date_range:
        start_ts, end_ts = date_range
        conditions = [f"local_type = 1", f"create_time >= {start_ts}", f"create_time < {end_ts}"]
    else:
        since_ts = int(time.time()) - days * 86400
        conditions = [f"local_type = 1", f"create_time >= {since_ts}"]

    # 按关键词过滤（在 hex 内容里找很麻烦，先拉出来再过滤）
    sql = (
        f"SELECT local_id, create_time, real_sender_id, "
        f"hex(message_content), hex(source) "
        f"FROM {table} "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY create_time DESC "
        f"LIMIT {limit * 5};"  # 多拉一些，后面再过滤
    )
    rows = query(sql)
    results = []
    for r in rows:
        if len(r) < 5:
            continue
        try:
            msg = (int(r[0]), int(r[1]), int(r[2]), r[3], r[4])
        except Exception:
            continue

        text = extract_text(msg[3])
        if not text or text.startswith("<"):
            continue

        # 按发送者过滤
        if person_wxid:
            raw = decode_raw(msg[3])
            sender_in_content = raw.split("\n")[0].rstrip(":") if "\n" in raw else ""
            if sender_in_content != person_wxid and msg[2] == 1 and person_wxid != "magicxinjx":
                continue
            if sender_in_content and sender_in_content != person_wxid:
                continue

        # 按关键词过滤
        if keyword and keyword.lower() not in text.lower():
            continue

        results.append(msg)
        if len(results) >= limit:
            break

    return list(reversed(results))  # 时间正序


def format_search_results(msgs: list[tuple], my_wxid: str) -> str:
    """把搜索结果格式化成文字，追加到 context"""
    if not msgs:
        return ""
    lines = ["[以下是从历史记录中检索到的相关消息]"]
    import datetime
    for msg in msgs:
        t = datetime.datetime.fromtimestamp(msg[1]).strftime("%m-%d %H:%M")
        raw = decode_raw(msg[3])
        sender_wxid = ""
        if "\n" in raw:
            first = raw.split("\n", 1)[0].strip().rstrip(":")
            import re
            if re.match(r"^[\w]{4,30}$", first):
                sender_wxid = first
        if sender_wxid and sender_wxid != my_wxid:
            name = get_name(sender_wxid)
        elif msg[2] == 1:
            name = "我"
        else:
            name = "对方"
        text = extract_text(msg[3])
        lines.append(f"[{t}] [{name}]: {text}")
    lines.append("[历史记录结束]")
    return "\n".join(lines)
