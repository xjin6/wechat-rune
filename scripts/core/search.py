"""Historical message search — xwechat_files schema (same as Mac WeChat).

Windows fix: sender extraction from compressed hex content is identical to Mac.
"""
import time
from core.decrypt import query
from core.reader import extract_text, decode_raw
from core.contacts import get_name


TIME_RANGES = {
    "今天": 1, "today": 1,
    "昨天": 2, "yesterday": 2,
    "上周": 7, "last week": 7, "这周": 7,
    "最近": 14, "recent": 14,
    "上个月": 30, "last month": 30,
    "最近一个月": 30,
}


def parse_days(time_str: str) -> int:
    if not time_str:
        return 30
    for k, v in TIME_RANGES.items():
        if k in time_str.lower():
            return v
    return 30


def parse_date_range(text: str):
    import re, datetime
    now = datetime.datetime.now()

    m = re.search(r'(\d+)月(\d+)[号日]', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year if month <= now.month else now.year - 1
        try:
            dt = datetime.datetime(year, month, day)
            start = int(dt.timestamp())
            return start, start + 86400
        except ValueError:
            pass

    m = re.search(r'(\d{1,2})[/-](\d{1,2})', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year if month <= now.month else now.year - 1
        try:
            dt = datetime.datetime(year, month, day)
            start = int(dt.timestamp())
            return start, start + 86400
        except ValueError:
            pass
    return None


def search_messages(table: str, person_wxid: str = None,
                    keyword: str = None, days: int = 30,
                    limit: int = 20, date_range=None) -> list[tuple]:
    if date_range:
        start_ts, end_ts = date_range
        conditions = [f"local_type = 1", f"create_time >= {start_ts}", f"create_time < {end_ts}"]
    else:
        since_ts = int(time.time()) - days * 86400
        conditions = [f"local_type = 1", f"create_time >= {since_ts}"]

    sql = (
        f"SELECT local_id, create_time, real_sender_id, "
        f"hex(message_content), hex(source) "
        f"FROM {table} "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY create_time DESC "
        f"LIMIT {limit * 5};"
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

        if person_wxid:
            raw = decode_raw(msg[3])
            sender_in_content = raw.split("\n")[0].rstrip(":") if "\n" in raw else ""
            if sender_in_content != person_wxid and msg[2] == 1:
                continue
            if sender_in_content and sender_in_content != person_wxid:
                continue

        if keyword and keyword.lower() not in text.lower():
            continue

        results.append(msg)
        if len(results) >= limit:
            break

    return list(reversed(results))


def fetch_context(table: str, local_id: int, window: int = 5) -> list[tuple]:
    rows = query(
        f"SELECT local_id, create_time, real_sender_id, "
        f"hex(message_content), hex(source) "
        f"FROM {table} WHERE local_type = 1 "
        f"AND local_id BETWEEN {local_id - window} AND {local_id + window} "
        f"ORDER BY create_time ASC;"
    )
    result = []
    for r in rows:
        if len(r) < 5:
            continue
        try:
            result.append((int(r[0]), int(r[1]), int(r[2]), r[3], r[4]))
        except Exception:
            pass
    return result


def format_search_results(msgs: list[tuple], my_wxid: str) -> str:
    if not msgs:
        return ""
    lines = ["[The following are relevant messages retrieved from history]"]
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
            name = "me"
        else:
            name = "other"
        text = extract_text(msg[3])
        lines.append(f"[{t}] [{name}]: {text}")
    lines.append("[End of historical records]")
    return "\n".join(lines)
