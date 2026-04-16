"""
Historical message search — used for RAG dynamic context expansion.

Windows schema differences from Mac:
  - Single MSG table with StrTalker filter (no per-conversation Msg_<md5> tables)
  - StrContent is plain text (no hex+zstd encoding)
  - IsSender field (0/1) instead of real_sender_id
  - Column names: localId, CreateTime, IsSender, StrContent, BytesExtra
"""
import time
from core.decrypt import query
from core.reader import extract_text, decode_raw
from core.contacts import get_name


# ── Time range parsing ────────────────────────────────────────────

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
    """Extract a specific date from text, return (start_ts, end_ts) or None."""
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


# ── Core search ───────────────────────────────────────────────────

def search_messages(table: str, person_wxid: str = None,
                    keyword: str = None, days: int = 30,
                    limit: int = 20, date_range=None) -> list[tuple]:
    """
    Search historical text messages in the specified conversation.
    `table` is the StrTalker value (wxid or chatroom ID).

    Returns tuples: (localId, CreateTime, IsSender, StrContent, hex_BytesExtra)
    """
    from config import MY_WXID

    if date_range:
        start_ts, end_ts = date_range
        conditions = [
            f"StrTalker = '{table}'",
            "Type = 1",
            f"CreateTime >= {start_ts}",
            f"CreateTime < {end_ts}",
        ]
    else:
        since_ts = int(time.time()) - days * 86400
        conditions = [
            f"StrTalker = '{table}'",
            "Type = 1",
            f"CreateTime >= {since_ts}",
        ]

    # Sender filter
    if person_wxid:
        if person_wxid == MY_WXID:
            conditions.append("IsSender = 1")
        else:
            conditions.append("IsSender = 0")  # received messages

    sql = (
        f"SELECT localId, CreateTime, IsSender, StrContent, hex(BytesExtra) "
        f"FROM MSG "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY CreateTime DESC "
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
        if not text or text.startswith('<'):
            continue

        if keyword and keyword.lower() not in text.lower():
            continue

        results.append(msg)
        if len(results) >= limit:
            break

    return list(reversed(results))


def fetch_context(table: str, local_id: int, window: int = 5) -> list[tuple]:
    """Fetch messages around a given localId for conversation context."""
    rows = query(
        f"SELECT localId, CreateTime, IsSender, StrContent, hex(BytesExtra) "
        f"FROM MSG WHERE StrTalker = '{table}' AND Type = 1 "
        f"AND localId BETWEEN {local_id - window} AND {local_id + window} "
        f"ORDER BY CreateTime ASC;"
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
    """Format search results as readable text to append to AI context."""
    if not msgs:
        return ""
    import datetime
    lines = ["[The following are relevant messages retrieved from history]"]
    for msg in msgs:
        t = datetime.datetime.fromtimestamp(msg[1]).strftime("%m-%d %H:%M")
        if msg[2] == 1:
            name = "me"
        else:
            name = "other"
        text = extract_text(msg[3])
        lines.append(f"[{t}] [{name}]: {text}")
    lines.append("[End of historical records]")
    return "\n".join(lines)
