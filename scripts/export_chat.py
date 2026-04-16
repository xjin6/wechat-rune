#!/usr/bin/env python3
"""
export_chat.py — Export WeChat chat history from Windows encrypted databases to Markdown

Usage:
  python export_chat.py --name "John"              # Search private chat by nickname/remark
  python export_chat.py --wxid wxid_xxxx           # Specify wxid directly
  python export_chat.py --name "SomeGroup" --group # Search group chat
  python export_chat.py --name "John" --out ~/Desktop/output.md

Windows WeChat database layout:
  %USERPROFILE%\\Documents\\WeChat Files\\<wxid>\\Msg\\
    MSG0.db       — messages  (table: MSG, filter by StrTalker)
    MicroMsg.db   — contacts  (table: Contact, UserName/NickName/Remark)

Dependencies:
  pip install zstandard sqlcipher3-binary
"""
import argparse, hashlib, json, os, re, sys, datetime
import xml.etree.ElementTree as ET

# ── Path helpers ──────────────────────────────────────────────────

def find_keys_file():
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        candidate = os.path.join(here, "keys", "wechat_keys.json")
        if os.path.exists(candidate):
            return candidate
        here = os.path.dirname(here)
    sys.exit("Cannot find keys/wechat_keys.json. Run extract_key_windows.py first.")


def detect_wxid_and_msg_dir(keys_file: str = None) -> tuple[str, str]:
    """
    Detect the logged-in WeChat account by scanning WeChat Files.
    Returns (wxid, msg_dir) where msg_dir = .../Msg/
    """
    # If keys_file is given, try to derive the wxid from it
    if keys_file and os.path.exists(keys_file):
        keys = json.load(open(keys_file))
        for rel_path in keys:
            # Windows key paths look like: wxid_xxx\Msg\MSG0.db
            parts = rel_path.replace('\\', '/').split('/')
            if len(parts) >= 2:
                wxid = parts[0]
                base = os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files")
                msg_dir = os.path.join(base, wxid, "Msg")
                if os.path.isdir(msg_dir):
                    return wxid, msg_dir

    # Fallback: scan WeChat Files for any logged-in account
    base = os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files")
    if not os.path.isdir(base):
        sys.exit(f"WeChat Files not found: {base}")
    for entry in sorted(os.listdir(base)):
        msg_dir = os.path.join(base, entry, "Msg")
        if os.path.isdir(msg_dir) and os.path.exists(os.path.join(msg_dir, "MSG0.db")):
            return entry, msg_dir
    sys.exit("No WeChat account data found. Please log in to WeChat first.")


# ── Database query ────────────────────────────────────────────────

def _query(db_path: str, key: str, sql: str) -> list[tuple]:
    """Query an encrypted SQLCipher database (tries SHA1 first, then SHA512)."""
    import tempfile, subprocess
    from config import SQLCIPHER_BIN

    def _via_sqlcipher3(sha1: bool) -> list[tuple] | None:
        try:
            import sqlcipher3 as _sc
            conn = _sc.connect(db_path)
            conn.execute(f"PRAGMA key = \"x'{key}'\"")
            conn.execute("PRAGMA cipher_page_size = 4096")
            if sha1:
                conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
                conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1")
            rows = conn.execute(sql.strip().rstrip(';')).fetchall()
            conn.close()
            return [tuple(str(c) if c is not None else '' for c in r) for r in rows]
        except Exception:
            return None

    for sha1 in (True, False):
        r = _via_sqlcipher3(sha1)
        if r is not None:
            return r

    # Binary fallback
    for sha1 in (True, False):
        if not os.path.exists(SQLCIPHER_BIN):
            break
        fd, tmp = tempfile.mkstemp(suffix='.sql')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(f'PRAGMA key = "x\'{key}\'";\n')
                f.write('PRAGMA cipher_page_size = 4096;\n')
                if sha1:
                    f.write('PRAGMA cipher_hmac_algorithm = HMAC_SHA1;\n')
                    f.write('PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1;\n')
                f.write('.separator "|||"\n')
                f.write(sql + '\n')
            r = subprocess.run([SQLCIPHER_BIN, db_path],
                               stdin=open(tmp, encoding='utf-8'),
                               capture_output=True, text=True, timeout=15)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        rows = []
        for line in r.stdout.splitlines():
            if line and line.strip() != 'ok':
                rows.append(tuple(line.split('|||')))
        if rows:
            return rows
    return []


def _get_key(keys_file: str, db_name: str) -> str:
    """Look up the encryption key for a given database filename."""
    keys = json.load(open(keys_file))
    db_lower = db_name.lower()
    for k, v in keys.items():
        if k.lower().endswith(db_lower):
            return v
    return ""


# ── Contact search ────────────────────────────────────────────────

def search_contacts(keys_file: str, msg_dir: str, name_query: str, group: bool = False):
    """Search contacts in MicroMsg.db, return [(wxid, display_name), ...]."""
    contact_db = os.path.join(msg_dir, "MicroMsg.db")
    key = _get_key(keys_file, "MicroMsg.db")
    if not key or not os.path.exists(contact_db):
        sys.exit("Cannot access MicroMsg.db. Run extract_key_windows.py first.")

    if group:
        where = (f"(NickName LIKE '%{name_query}%' OR Remark LIKE '%{name_query}%') "
                 f"AND UserName LIKE '%@chatroom%'")
    else:
        where = (f"(NickName LIKE '%{name_query}%' OR Remark LIKE '%{name_query}%') "
                 f"AND UserName NOT LIKE '%@chatroom%'")

    rows = _query(contact_db, key,
                  f"SELECT UserName, NickName, Remark FROM Contact WHERE {where} LIMIT 20;")
    results = []
    for row in rows:
        if len(row) < 3:
            continue
        wxid = row[0].strip()
        nick = row[1].strip()
        remark = row[2].strip()
        display = remark or nick or wxid
        results.append((wxid, display))
    return results


# ── Message formatting ────────────────────────────────────────────

def _parse_xml(text: str):
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        try:
            return ET.fromstring(f"<root>{text}</root>")
        except ET.ParseError:
            return None


def _attr_anywhere(root, attr: str) -> str:
    for el in root.iter():
        v = el.get(attr, "")
        if v:
            return v
    return ""


def format_msg(content: str, msg_type: int, voice_map: dict = None, ts: int = None):
    """
    Format a Windows WeChat message by type.
    Returns (text, is_system); text=None means skip.

    Windows: StrContent is already plain text for Type=1.
             Type=49 (app/link/quote) has XML in StrContent.
    """
    is_system = False

    if msg_type == 1:
        text = content.strip()
        if not text or text.startswith('<'):
            return None, False
        return text, False

    elif msg_type == 3:
        return "[图片]", False

    elif msg_type == 34:
        # Voice — Windows may put duration in StrContent XML or leave it empty
        if content and content.strip().startswith('<'):
            root = _parse_xml(content.strip())
            vlen = _attr_anywhere(root, "voicelength") if root is not None else ""
            if vlen:
                try:
                    secs = max(1, round(int(vlen) / 1000))
                    tag = f"[语音 {secs}s]"
                except Exception:
                    tag = "[语音]"
            else:
                tag = "[语音]"
        else:
            tag = "[语音]"

        if voice_map and ts is not None:
            entry = voice_map.get(str(ts))
            if entry and entry.get("text"):
                transcript = entry["text"].strip()
                if transcript and not transcript.startswith("[转录失败"):
                    return f"{tag} {transcript}", False
        return tag, False

    elif msg_type == 43:
        secs = ""
        if content and content.strip().startswith('<'):
            root = _parse_xml(content.strip())
            secs = _attr_anywhere(root, "playlength") if root is not None else ""
        if secs:
            try:
                return f"[视频 {int(secs)}s]", False
            except Exception:
                pass
        title = content.strip() if content and not content.strip().startswith('<') else ""
        return f"[视频{': ' + title if title else ''}]", False

    elif msg_type == 47:
        return "[表情包]", False

    elif msg_type % 65536 == 49:
        # App messages: links, file shares, quoted replies, mini-programs, transfers, etc.
        if not content or not content.strip().startswith('<'):
            return "[链接]", False
        root = _parse_xml(content.strip())
        if root is None:
            return "[链接]", False

        title_el = root.find(".//title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        des_el = root.find(".//des")
        des = (des_el.text or "").strip() if des_el is not None else ""
        type_el = root.find(".//type")
        subtype = int(type_el.text) if type_el is not None and type_el.text and type_el.text.strip().isdigit() else 0
        src_el = root.find(".//sourcedisplayname")
        source = (src_el.text or "").strip() if src_el is not None else ""

        # Quoted reply (subtype=57)
        refermsg = root.find(".//refermsg")
        if refermsg is not None:
            quoted_content = refermsg.find("content")
            quoted_text = (quoted_content.text or "").strip() if quoted_content is not None else ""
            if len(quoted_text) > 60:
                quoted_text = quoted_text[:60] + "…"
            if title and quoted_text:
                return f"{title}\n> {quoted_text}", False
            elif title:
                return f"[引用] {title}", False

        label = {
            5: "公众号", 6: "文件", 8: "表情", 19: "聊天记录",
            33: "小程序", 36: "小程序", 62: "互动", 2000: "转账", 2001: "红包",
        }.get(subtype, "链接")

        if subtype == 2000:
            feedesc_el = root.find(".//feedesc")
            amount = (feedesc_el.text or "").strip() if feedesc_el is not None else ""
            return f"[转账 {amount}]" if amount else "[转账]", False

        if subtype == 2001:
            memo = ""
            for tag_name in ("sendertitle", "pay_memo"):
                el = root.find(f".//{tag_name}")
                if el is not None and el.text and el.text.strip():
                    memo = el.text.strip()
                    break
            return f"[红包 {memo}]" if memo else "[红包]", False

        src_tag = f" | {source}" if source else ""
        des_tag = f" {des[:40]}" if des and not title else ""
        if title:
            return f"[{label}{src_tag}] {title}{des_tag}", False
        elif des:
            return f"[{label}{src_tag}] {des[:60]}", False
        return f"[{label}]", False

    elif msg_type == 10000:
        text = content.strip()
        if text and not text.startswith('<'):
            return text, True
        if text and text.startswith('<'):
            root = _parse_xml(text)
            if root is not None:
                systype = root.get("type", "")
                if systype == "revokemsg":
                    c = root.find(".//content")
                    if c is not None and c.text:
                        return c.text.strip(), True
                if systype == "pat":
                    tmpl = root.find(".//template")
                    if tmpl is not None:
                        raw_text = ET.tostring(tmpl, encoding="unicode", method="text").strip()
                        if raw_text:
                            return raw_text, True
                raw_text = ET.tostring(root, encoding="unicode", method="text").strip()
                if raw_text:
                    return raw_text, True
        return None, False

    return None, False


# ── Contact name map ──────────────────────────────────────────────

def _build_nick_map(keys_file: str, msg_dir: str) -> dict[str, str]:
    contact_db = os.path.join(msg_dir, "MicroMsg.db")
    key = _get_key(keys_file, "MicroMsg.db")
    if not key:
        return {}
    rows = _query(contact_db, key, "SELECT UserName, NickName FROM Contact;")
    nick_map = {}
    for row in rows:
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            nick_map[row[0].strip()] = row[1].strip()
    return nick_map


# ── Core export ───────────────────────────────────────────────────

def export_chat(wxid: str, display_name: str, keys_file: str, msg_dir: str,
                out_path: str, voice_map: dict = None):
    """Export all messages with a given contact to a Markdown file."""
    nick_map = _build_nick_map(keys_file, msg_dir)

    # Collect all MSG databases: MSG0.db, MSG1.db, ...
    msg_db_files = sorted(
        [f for f in os.listdir(msg_dir) if re.match(r'MSG\d+\.db$', f, re.IGNORECASE)],
        key=lambda x: int(re.search(r'\d+', x).group())
    )

    EXPORT_TYPES = (1, 3, 34, 43, 47, 10000)

    all_msgs: list[tuple] = []
    seen_server_ids: set[int] = set()
    my_wxid = detect_wxid_and_msg_dir(keys_file)[0]

    for db_name in msg_db_files:
        db_path = os.path.join(msg_dir, db_name)
        key = _get_key(keys_file, db_name)
        if not key:
            continue

        type_list = ",".join(str(t) for t in EXPORT_TYPES)
        rows = _query(db_path, key,
            f"SELECT CreateTime, IsSender, StrContent, Type, MsgSvrID "
            f"FROM MSG "
            f"WHERE StrTalker = '{wxid}' "
            f"AND (Type IN ({type_list}) OR Type % 65536 = 49) "
            f"ORDER BY CreateTime ASC;"
        )
        for row in rows:
            if len(row) < 5:
                continue
            try:
                ts       = int(row[0])
                is_me    = int(row[2]) == 1
                content  = row[2]       # StrContent — direct text
                msg_type = int(row[3])
                svr_id   = int(row[4]) if row[4] else 0

                text, is_system = format_msg(content, msg_type, voice_map=voice_map, ts=ts)
                if text is None:
                    continue
                if svr_id and svr_id in seen_server_ids:
                    continue
                if svr_id:
                    seen_server_ids.add(svr_id)

                # is_me for private chats: IsSender field; row[1] = IsSender
                is_me_flag = None if is_system else (int(row[1]) == 1)
                all_msgs.append((ts, is_me_flag, text))
            except Exception:
                pass

    if not all_msgs:
        print(f"Warning: no messages found for {wxid}")
        sys.exit(1)

    all_msgs.sort(key=lambda x: x[0])

    lines = [f"# 与{display_name}的微信聊天记录\n",
             f"共 {len(all_msgs)} 条消息\n\n---\n"]
    current_date = None
    my_name = nick_map.get(my_wxid, "我")

    for ts, is_me, text in all_msgs:
        dt = datetime.datetime.fromtimestamp(ts)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
        if date_str != current_date:
            current_date = date_str
            lines.append(f"\n## {date_str}\n")
        if is_me is None:
            lines.append(f"*{time_str} {text}*\n")
        else:
            sender = my_name if is_me else display_name
            lines.append(f"**{time_str} {sender}**\n{text}\n")

    content = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    size_kb = os.path.getsize(out_path) // 1024
    print(f"Exported {len(all_msgs)} messages -> {out_path} ({size_kb} KB)")


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WeChat Windows chat history export")
    parser.add_argument("--name",  help="Contact remark or nickname (fuzzy search)")
    parser.add_argument("--wxid",  help="Specify wxid directly (skip search)")
    parser.add_argument("--group", action="store_true", help="Search group chats")
    parser.add_argument("--out",   help="Output Markdown path (default: project root)")
    parser.add_argument("--voice-json", help="Voice transcription JSON file")
    args = parser.parse_args()

    if not args.name and not args.wxid:
        parser.print_help()
        sys.exit(1)

    keys_file = find_keys_file()
    my_wxid, msg_dir = detect_wxid_and_msg_dir(keys_file)

    if args.wxid:
        target_wxid  = args.wxid
        display_name = args.wxid
    else:
        results = search_contacts(keys_file, msg_dir, args.name, group=args.group)
        if not results:
            print(f"No contacts found matching '{args.name}'")
            sys.exit(1)
        if len(results) == 1:
            target_wxid, display_name = results[0]
            print(f"Found: {display_name} ({target_wxid})")
        else:
            print("Multiple contacts found, please choose:")
            for i, (wxid, name) in enumerate(results):
                print(f"  [{i+1}] {name} ({wxid})")
            choice = input("Enter number: ").strip()
            try:
                idx = int(choice) - 1
                target_wxid, display_name = results[idx]
            except (ValueError, IndexError):
                sys.exit("Invalid selection")

    out_path = os.path.expanduser(args.out) if args.out else None
    if not out_path:
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', display_name)
        repo_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_path  = os.path.join(repo_dir, f"{safe_name}_聊天记录.md")

    voice_map = None
    if args.voice_json:
        vj = os.path.expanduser(args.voice_json)
        if os.path.exists(vj):
            with open(vj, encoding="utf-8") as f:
                voice_map = json.load(f)
            print(f"Loaded voice transcriptions: {len(voice_map)} entries")

    export_chat(target_wxid, display_name, keys_file, msg_dir, out_path, voice_map=voice_map)


if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
