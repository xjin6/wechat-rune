"""Read WeChat messages — xwechat_files schema (same as Mac WeChat).

Windows Weixin uses the same DB layout as Mac:
  table:  Msg_<md5(wxid)>
  fields: local_id, create_time, real_sender_id, hex(message_content), hex(source)
"""
import zstandard, xml.etree.ElementTree as ET, re
from config import MY_WXID, MAX_HISTORY
from core.decrypt import query


def _decode_hex(hex_str: str) -> bytes:
    try:
        return bytes.fromhex(hex_str) if hex_str else b''
    except ValueError:
        return hex_str.encode('utf-8', errors='replace')


def _decode(raw: bytes) -> str:
    if raw[:4] == b'\x28\xb5\x2f\xfd':
        try:
            return zstandard.decompress(raw).decode('utf-8', errors='replace')
        except Exception:
            return ''
    return raw.decode('utf-8', errors='replace')


def decode_raw(hex_content: str) -> str:
    return _decode(_decode_hex(hex_content))


def extract_text(hex_content: str) -> str:
    raw  = _decode_hex(hex_content)
    text = _decode(raw)
    if '\n' in text:
        first_line, rest = text.split('\n', 1)
        if re.match(r'^[\w]{4,30}:$', first_line.strip()):
            return rest.strip()
    return text.strip()


def is_at_me(hex_source: str) -> bool:
    raw = _decode(_decode_hex(hex_source))
    if not raw:
        return False
    try:
        root = ET.fromstring(raw)
        return MY_WXID in root.findtext('atuserlist', '')
    except Exception:
        return False


def get_max_id(table: str) -> int:
    rows = query(f'SELECT MAX(local_id) FROM {table} WHERE local_type = 1;')
    try:
        return int(rows[0][0]) if rows and rows[0][0] else 0
    except Exception:
        return 0


def get_new_messages(table: str, after_id: int) -> list[tuple]:
    rows = query(
        f'SELECT local_id, create_time, real_sender_id, '
        f'hex(message_content), hex(source) '
        f'FROM {table} '
        f'WHERE local_type = 1 AND local_id > {after_id} '
        f'ORDER BY create_time ASC;'
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


def load_initial_history(table: str) -> list[tuple]:
    rows = query(
        f'SELECT local_id, create_time, real_sender_id, '
        f'hex(message_content), hex(source) '
        f'FROM {table} '
        f'WHERE local_type = 1 '
        f'ORDER BY create_time DESC '
        f'LIMIT {MAX_HISTORY};'
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
