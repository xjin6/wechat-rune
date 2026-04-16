"""
Contact name lookup using Windows MicroMsg.db.

Windows schema (MicroMsg.db → Contact table):
  UserName   TEXT   -- wxid or phone number
  NickName   TEXT   -- display name set by the contact
  Remark     TEXT   -- nickname you set for this contact (preferred)
  Type       INT    -- contact type
  ...

Group membership (MicroMsg.db → ChatRoom table):
  ChatRoomName  TEXT   -- chatroom ID (xxx@chatroom)
  MemberList    TEXT   -- semicolon-separated wxid list
  ...
"""
import re
from core.decrypt import query_contact

_cache: dict[str, str] = {}


def get_name(wxid: str) -> str:
    """Return display name for a wxid (remark > nickname > wxid). Result is cached."""
    if not wxid:
        return wxid
    if wxid in _cache:
        return _cache[wxid]

    rows = query_contact(
        f"SELECT NickName, Remark FROM Contact WHERE UserName = '{wxid}' LIMIT 1;"
    )
    for row in rows:
        if len(row) >= 2:
            nick, remark = row[0].strip(), row[1].strip()
            if remark and nick and remark != nick:
                name = f"{remark}({nick})"
            else:
                name = remark or nick or wxid
            _cache[wxid] = name
            return name

    _cache[wxid] = wxid   # cache miss — fall back to wxid itself
    return wxid


def find_wxid(name: str) -> str | None:
    """Reverse-lookup wxid by nickname or remark (fuzzy)."""
    # Check cache first
    for wxid, cached_name in _cache.items():
        parts = re.split(r'[（(）)]', cached_name)
        if any(name in p for p in parts):
            return wxid

    rows = query_contact(
        f"SELECT UserName, NickName, Remark FROM Contact "
        f"WHERE NickName LIKE '%{name}%' OR Remark LIKE '%{name}%' LIMIT 1;"
    )
    for row in rows:
        if len(row) >= 3:
            wxid, nick, remark = row[0].strip(), row[1].strip(), row[2].strip()
            if remark and nick and remark != nick:
                _cache[wxid] = f"{remark}({nick})"
            else:
                _cache[wxid] = remark or nick or wxid
            return wxid
    return None


def preload_from_messages(talker_id: str, limit: int = 500):
    """Pre-warm the contact cache for participants in a conversation.

    For group chats: load the member list from the ChatRoom table in MicroMsg.db.
    For private chats: load the single contact.
    """
    if '@chatroom' in talker_id:
        rows = query_contact(
            f"SELECT MemberList FROM ChatRoom WHERE ChatRoomName = '{talker_id}' LIMIT 1;"
        )
        for row in rows:
            if row and row[0]:
                member_wxids = [w.strip() for w in row[0].split(';') if w.strip()]
                if member_wxids:
                    preload(member_wxids)
    elif talker_id and not talker_id.startswith('gh_'):
        preload([talker_id])


def preload(wxids: list[str]):
    """Bulk-load contact names to reduce individual DB queries."""
    missing = [w for w in wxids if w not in _cache]
    if not missing:
        return

    ids_str = ','.join(f"'{w}'" for w in missing)
    rows = query_contact(
        f"SELECT UserName, NickName, Remark FROM Contact WHERE UserName IN ({ids_str});"
    )
    found = set()
    for row in rows:
        if len(row) >= 3:
            wxid, nick, remark = row[0].strip(), row[1].strip(), row[2].strip()
            if remark and nick and remark != nick:
                _cache[wxid] = f"{remark}({nick})"
            else:
                _cache[wxid] = remark or nick or wxid
            found.add(wxid)
    # Cache misses too, to avoid repeated lookups
    for w in missing:
        if w not in found:
            _cache[w] = w
