"""
Read WeChat messages from Windows MSG database.

Windows MSG table schema (MSG0.db):
  localId     INTEGER PRIMARY KEY
  TalkerId    INT
  MsgSvrID    INT
  Type        INT      -- 1=text, 3=image, 34=voice, 43=video, 47=emoji, 49=app, 10000=system
  SubType     INT
  IsSender    INT      -- 1=I sent it, 0=received
  CreateTime  INT      -- Unix timestamp
  StrTalker   TEXT     -- Conversation ID (wxid for private, xxx@chatroom for group)
  StrContent  TEXT     -- Message text (plain text for Type=1, XML for Type=49)
  BytesExtra  BLOB     -- Extra metadata (at-mention info encoded as protobuf)
  ...

Message tuples used throughout the codebase:
  (localId, CreateTime, IsSender, StrContent, hex(BytesExtra))
  pos 0     pos 1       pos 2     pos 3       pos 4
"""
import xml.etree.ElementTree as ET
from config import MY_WXID, MAX_HISTORY
from core.decrypt import query


def decode_raw(content: str) -> str:
    """Windows: StrContent is already plain text — return as-is."""
    return content if content else ''


def extract_text(content: str) -> str:
    """Windows: StrContent is already plain text — strip whitespace."""
    return content.strip() if content else ''


def is_at_me(bytes_extra_hex: str) -> bool:
    """Check if this message @mentions the current user.

    WeChat stores at-mention info in BytesExtra as a protobuf blob.
    A simple byte-search for the user's wxid is a reliable and dependency-free approach.
    """
    if not bytes_extra_hex or not MY_WXID:
        return False
    try:
        data = bytes.fromhex(bytes_extra_hex)
        return MY_WXID.encode('utf-8') in data
    except Exception:
        return False


def get_max_id(talker_id: str) -> int:
    """Return the max localId for a conversation (used to track last seen message)."""
    rows = query(
        f"SELECT MAX(localId) FROM MSG WHERE StrTalker = '{talker_id}' AND Type = 1;"
    )
    try:
        return int(rows[0][0]) if rows and rows[0][0] else 0
    except Exception:
        return 0


def get_new_messages(talker_id: str, after_id: int) -> list[tuple]:
    """Return new text messages for a conversation since after_id."""
    rows = query(
        f"SELECT localId, CreateTime, IsSender, StrContent, hex(BytesExtra) "
        f"FROM MSG "
        f"WHERE StrTalker = '{talker_id}' AND Type = 1 AND localId > {after_id} "
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


def load_initial_history(talker_id: str) -> list[tuple]:
    """Load the most recent MAX_HISTORY text messages to seed the in-memory deque."""
    rows = query(
        f"SELECT localId, CreateTime, IsSender, StrContent, hex(BytesExtra) "
        f"FROM MSG "
        f"WHERE StrTalker = '{talker_id}' AND Type = 1 "
        f"ORDER BY CreateTime DESC "
        f"LIMIT {MAX_HISTORY};"
    )
    result = []
    for r in rows:
        if len(r) < 5:
            continue
        try:
            result.append((int(r[0]), int(r[1]), int(r[2]), r[3], r[4]))
        except Exception:
            pass
    return list(reversed(result))
