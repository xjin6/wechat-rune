#!/usr/bin/env python3
"""
export_chat.py — Export WeChat private or group chat history from encrypted databases to Markdown

Usage:
  python3 export_chat.py --name "John"              # Search private chat by remark/nickname
  python3 export_chat.py --wxid wxid_xxxx           # Specify wxid directly
  python3 export_chat.py --name "SomeGroup" --group # Search group chat
  python3 export_chat.py --name "John" --out ~/Desktop/output.md

Dependencies: sqlcipher (brew install sqlcipher), zstandard (pip install zstandard)
"""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile, datetime
import xml.etree.ElementTree as ET

try:
    import zstandard
except ImportError:
    sys.exit("Please install first: pip install zstandard")

# Message types to export (type=49 has high-bit flags, matched via modulo)
EXPORT_TYPES_EXACT = (1, 3, 34, 43, 47, 50, 10000)
# type=49 matched via local_type % 65536 = 49 to cover all variants

# ── Path auto-detection ────────────────────────────────────────────────────

def find_keys_file():
    """Search upward from the script location for keys/wechat_keys.json."""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        candidate = os.path.join(here, "keys", "wechat_keys.json")
        if os.path.exists(candidate):
            return candidate
        here = os.path.dirname(here)
    sys.exit("Cannot find keys/wechat_keys.json. Please complete the key extraction step first.")

def detect_wxid_and_db_dir(keys_file):
    """Extract wxid and DB root directory from the path keys in keys.json."""
    keys = json.load(open(keys_file))
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    # Take any key path, e.g. magicxinjx_c092/db_storage/...
    for rel_path in keys:
        parts = rel_path.split("/")
        if len(parts) > 1:
            folder = parts[0]          # e.g. magicxinjx_c092
            wxid = folder.split("_")[0]
            db_dir = os.path.join(base, folder, "db_storage")
            return wxid, db_dir
    sys.exit("Cannot parse wxid from wechat_keys.json. The file may be corrupted.")

SQLCIPHER = os.environ.get("SQLCIPHER_BIN", "/opt/homebrew/opt/sqlcipher/bin/sqlcipher")

# ── SQLCipher query helper ───────────────────────────────────────────────────

def sqlcipher_query(db_path, key, sql):
    if not os.path.exists(db_path):
        return []
    full_sql = f'PRAGMA key = "x\'{key}\'";\nPRAGMA cipher_page_size = 4096;\n.separator "|||"\n{sql}\n'
    fd, tmp = tempfile.mkstemp(suffix=".sql")
    with os.fdopen(fd, "w") as f:
        f.write(full_sql)
    try:
        r = subprocess.run(
            [SQLCIPHER, db_path], stdin=open(tmp),
            capture_output=True, timeout=15, errors="replace",
            text=True
        )
    finally:
        os.unlink(tmp)
    rows = []
    for line in r.stdout.splitlines():
        if line and line != "ok":
            rows.append(tuple(line.split("|||")))
    return rows

# ── Contact search ───────────────────────────────────────────────────────────

def search_contacts(keys_file, db_dir, name_query, group=False):
    """Search contacts by nickname/remark, return [(wxid, display_name), ...]."""
    key = next((v for k, v in json.load(open(keys_file)).items()
                if "contact/contact.db" in k), "")
    if not key:
        sys.exit("Cannot find the key for contact.db. Please re-extract the keys.")
    contact_db = os.path.join(db_dir, "contact", "contact.db")

    if group:
        where = f"(nick_name LIKE '%{name_query}%' OR remark LIKE '%{name_query}%') AND username LIKE '%@chatroom%'"
    else:
        where = f"(nick_name LIKE '%{name_query}%' OR remark LIKE '%{name_query}%') AND username NOT LIKE '%@chatroom%'"

    rows = sqlcipher_query(
        contact_db, key,
        f"SELECT username, nick_name, remark FROM contact WHERE {where} LIMIT 20;"
    )
    results = []
    for row in rows:
        if len(row) < 3:
            continue
        wxid, nick, remark = row[0].strip(), row[1].strip(), row[2].strip()
        display = nick or remark or wxid
        results.append((wxid, display))
    return results

# ── Message decoding and formatting ──────────────────────────────────────────

def _decode_raw(hex_str) -> str:
    """Decode hex_str to a UTF-8 string (handles zstd compression)."""
    if not hex_str:
        return ""
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return hex_str
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        try:
            text = zstandard.decompress(raw).decode("utf-8", errors="replace")
        except Exception:
            return ""
    else:
        text = raw.decode("utf-8", errors="replace")
    # Group messages have wxid: as the first line — strip it
    if "\n" in text:
        first, rest = text.split("\n", 1)
        if re.match(r"^[\w]{4,30}:$", first.strip()):
            return rest.strip()
    return text.strip()


def _parse_xml(text: str):
    """Leniently parse XML, return root Element or None."""
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        try:
            return ET.fromstring(f"<root>{text}</root>")
        except ET.ParseError:
            return None


def _attr_anywhere(root, attr: str) -> str:
    """Find the first element with the given attr in the entire XML tree and return its value."""
    for el in root.iter():
        v = el.get(attr, "")
        if v:
            return v
    return ""


def _refermsg_meta(refermsg, nick_map=None) -> str:
    """Extract sender and time from a refermsg element, return a string like 'John 03-15 22:30'."""
    display = ""
    # Prefer nick_map (wxid -> nickname)
    fu = refermsg.find("fromusr")
    wxid = fu.text.strip() if fu is not None and fu.text else ""
    if nick_map and wxid and wxid in nick_map:
        display = nick_map[wxid]
    if not display:
        dn = refermsg.find("displayname")
        if dn is not None and dn.text and dn.text.strip():
            display = dn.text.strip()
    if not display:
        display = wxid
    ct = refermsg.find("createtime")
    timestr = ""
    if ct is not None and ct.text:
        try:
            timestr = datetime.datetime.fromtimestamp(int(ct.text)).strftime("%m-%d %H:%M")
        except Exception:
            pass
    parts = [p for p in [display, timestr] if p]
    return " ".join(parts)


def _format_refermsg(refermsg, prefix=">", nick_map=None) -> str:
    """Recursively format refermsg, return a quote block with > prefix (includes metadata and nested quotes)."""
    meta = _refermsg_meta(refermsg, nick_map)
    meta_line = f"{prefix} *{meta}*\n" if meta else ""

    content_el = refermsg.find("content")
    body = ""
    nested = ""

    if content_el is not None and content_el.text:
        raw = content_el.text.strip()
        if raw.startswith("<"):
            inner = _parse_xml(raw)
            if inner is not None:
                inner_title = inner.find(".//title")
                if inner_title is not None and inner_title.text:
                    body = inner_title.text.strip()
                elif inner.find(".//emoji") is not None:
                    body = "[表情包]"
                elif inner.find(".//voicemsg") is not None:
                    vl = _attr_anywhere(inner, "voicelength")
                    if vl:
                        try:
                            body = f"[语音 {max(1, round(int(vl) / 1000))}s]"
                        except Exception:
                            body = "[语音]"
                    else:
                        body = "[语音]"
                elif inner.find(".//videomsg") is not None:
                    pl = _attr_anywhere(inner, "playlength")
                    if pl:
                        try:
                            body = f"[视频 {int(pl)}s]"
                        except Exception:
                            body = "[视频]"
                    else:
                        body = "[视频]"
                elif inner.find(".//img") is not None:
                    body = "[图片]"
                else:
                    body = "[消息]"
                # Nested quote
                inner_refer = inner.find(".//refermsg")
                if inner_refer is not None:
                    nested = _format_refermsg(inner_refer, prefix + ">", nick_map)
            else:
                body = "[消息]"
        else:
            body = raw

    if len(body) > 80:
        body = body[:80] + "…"

    result = meta_line + f"{prefix} {body}" if body else meta_line.rstrip()
    if nested:
        result += "\n" + nested
    return result


def format_msg(hex_str: str, local_type: int, voice_map: dict = None, ts: int = None, nick_map: dict = None):
    """
    Format message content by type into a readable string.
    Returns (text, is_system); text=None means skip this message.
    voice_map: {str(ts): {"text": ...}} used to replace voice placeholders with transcribed text.
    """
    text = _decode_raw(hex_str)
    is_system = False

    if local_type == 1:
        # Plain text
        if not text or text.startswith("<"):
            return None, False
        return text, False

    elif local_type == 3:
        return "[图片]", False

    elif local_type == 34:
        # Voice message — prefer transcribed text, fall back to duration placeholder
        root = _parse_xml(text)
        vlen = _attr_anywhere(root, "voicelength") if root is not None else ""
        secs = None
        if vlen:
            try:
                secs = max(1, round(int(vlen) / 1000))
            except Exception:
                pass
        tag = f"[语音 {secs}s]" if secs else "[语音]"
        if voice_map and ts is not None:
            entry = voice_map.get(str(ts))
            if entry and entry.get("text"):
                transcript = entry["text"].strip()
                if transcript and not transcript.startswith("[转录失败"):
                    corrections = entry.get("corrections", [])
                    suffix = f" <!-- 纠正: {', '.join(corrections)} -->" if corrections else ""
                    return f"{tag} {transcript}{suffix}", False
        if secs:
            return tag, False
        return "[语音]", False

    elif local_type == 43:
        # Video — XML contains playlength (seconds)
        root = _parse_xml(text)
        secs = _attr_anywhere(root, "playlength") if root is not None else ""
        if secs:
            try:
                return f"[视频 {int(secs)}s]", False
            except Exception:
                pass
        return "[视频]", False

    elif local_type == 47:
        return "[表情包]", False

    elif local_type % 65536 == 49:
        # type=49 subtypes: quoted replies, official accounts, mini programs, files, transfers, etc.
        root = _parse_xml(text)
        if root is not None:
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
                quoted = _format_refermsg(refermsg, ">", nick_map)
                if quoted and title:
                    return f"{title}\n{quoted}", False
                elif quoted:
                    return quoted, False
                elif title:
                    return f"[引用] {title}", False

            # Subtype labels
            label = {
                5: "公众号",
                6: "文件",
                8: "表情",
                19: "聊天记录",
                33: "小程序",
                36: "小程序",
                62: "互动",
                2000: "转账",
                2001: "红包",
            }.get(subtype, "链接")

            # Special handling for transfers/red packets
            if subtype == 2000:
                feedesc_el = root.find(".//feedesc")
                amount = (feedesc_el.text or "").strip() if feedesc_el is not None else ""
                return f"[转账 {amount}]" if amount else "[转账]", False
            if subtype == 2001:
                # Memo is in sendertitle or pay_memo; des is a fixed template
                memo = ""
                for tag in ("sendertitle", "pay_memo"):
                    el = root.find(f".//{tag}")
                    if el is not None and el.text and el.text.strip():
                        memo = el.text.strip()
                        break
                return f"[红包 {memo}]" if memo else "[红包]", False

            # Source (official account name / mini program name)
            src_tag = f" | {source}" if source else ""
            # Description/summary
            des_tag = f" {des[:40]}" if des and not title else ""

            if title:
                return f"[{label}{src_tag}] {title}{des_tag}", False
            elif des:
                return f"[{label}{src_tag}] {des[:60]}", False
            else:
                return f"[{label}]", False
        return "[链接]", False

    elif local_type == 50:
        # Call — find duration and type
        root = _parse_xml(text)
        if root is not None:
            for el in root.iter():
                duration = el.get("duration", "")
                if duration:
                    try:
                        secs = int(duration)
                        # invitetype: 0=voice, 1=video
                        invite = el.get("invitetype", el.get("msg_type", "0"))
                        call_type = "视频通话" if invite in ("1", "3") else "语音通话"
                        if secs > 0:
                            return f"[{call_type} {secs}s]", False
                        else:
                            return f"[{call_type} 未接通]", False
                    except Exception:
                        pass
        return "[通话]", False

    elif local_type == 10000:
        # System messages (friend request, recall, pat-pat, red packet opened, etc.)
        if text and not text.startswith("<"):
            return text, True   # is_system=True, no sender
        # XML system messages — extract readable content
        if text and text.startswith("<"):
            root = _parse_xml(text)
            if root is not None:
                systype = root.get("type", "")
                # Recall messages: <sysmsg type="revokemsg"><revokemsg><content>...</content>
                if systype == "revokemsg":
                    content_el = root.find(".//content")
                    if content_el is not None and content_el.text:
                        return content_el.text.strip(), True
                # Pat-pat messages: <sysmsg type="pat"><pat><template>...</template>
                if systype == "pat":
                    tmpl = root.find(".//template")
                    if tmpl is not None:
                        # Template contains XML tags like <_wc_custom_link_>, strip them
                        raw = ET.tostring(tmpl, encoding="unicode", method="text").strip()
                        if raw:
                            return raw, True
                # Red packet opened: contains <img> tags, extract text
                raw_text = ET.tostring(root, encoding="unicode", method="text").strip()
                if raw_text:
                    return raw_text, True
        return None, False

    return None, False

# ── Core export logic ────────────────────────────────────────────────────────

def _build_nick_map(keys_file, db_dir, my_wxid):
    """Build a wxid -> nick_name mapping from contact.db."""
    keys = json.load(open(keys_file))
    contact_key = next((v for k, v in keys.items() if "contact/contact.db" in k), "")
    if not contact_key:
        return {}
    contact_db = os.path.join(db_dir, "contact", "contact.db")
    rows = sqlcipher_query(contact_db, contact_key,
        "SELECT username, nick_name FROM contact;")
    nick_map = {}
    for row in rows:
        if len(row) >= 2:
            wid, nick = row[0].strip(), row[1].strip()
            if wid and nick:
                nick_map[wid] = nick
    # Own nickname: look up self in contacts, or use wxid as fallback
    if my_wxid not in nick_map:
        nick_map[my_wxid] = my_wxid
    return nick_map


def export_chat(wxid, display_name, keys_file, db_dir, out_path, voice_map=None):
    keys = json.load(open(keys_file))
    table = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()

    my_wxid = detect_wxid_and_db_dir(keys_file)[0]
    nick_map = _build_nick_map(keys_file, db_dir, my_wxid)

    # All message_N.db files
    msg_db_dir = os.path.join(db_dir, "message")
    db_files = sorted(
        [f for f in os.listdir(msg_db_dir)
         if re.match(r"message_\d+\.db$", f)],
        key=lambda x: int(re.search(r"\d+", x).group())
    )

    all_msgs = []
    seen = set()

    for db_name in db_files:
        db_path = os.path.join(msg_db_dir, db_name)
        key_pattern = f"message/{db_name}"
        key = next((v for k, v in keys.items() if key_pattern in k), "")
        if not key:
            continue

        # Verify this DB has the target table
        has_table = sqlcipher_query(
            db_path, key,
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';"
        )
        if not has_table:
            continue

        # Build rowid -> wxid mapping from Name2Id table
        name2id_rows = sqlcipher_query(
            db_path, key,
            "SELECT rowid, user_name FROM Name2Id ORDER BY rowid;"
        )
        my_wxid_prefix = detect_wxid_and_db_dir(keys_file)[0]
        my_sender_ids = set()
        for row in name2id_rows:
            if len(row) == 2:
                try:
                    rid = int(row[0])
                    uname = row[1].strip()
                    if my_wxid_prefix in uname:
                        my_sender_ids.add(rid)
                except Exception:
                    pass

        # Fetch messages (multiple types, type=49 matched via modulo for all variants)
        type_list = ",".join(str(t) for t in EXPORT_TYPES_EXACT)
        rows = sqlcipher_query(
            db_path, key,
            f"SELECT create_time, real_sender_id, hex(message_content), local_type, server_id "
            f"FROM {table} WHERE local_type IN ({type_list}) "
            f"OR local_type % 65536 = 49 ORDER BY create_time ASC;"
        )
        for row in rows:
            if len(row) < 5:
                continue
            try:
                ts = int(row[0])
                sender_id = int(row[1])
                local_type = int(row[3])
                svr_id = int(row[4]) if row[4] else 0
                text, is_system = format_msg(row[2], local_type, voice_map=voice_map, ts=ts, nick_map=nick_map)
                if text is None:
                    continue
                if svr_id in seen:
                    continue
                seen.add(svr_id)
                is_me = None if is_system else (sender_id in my_sender_ids)
                all_msgs.append((ts, is_me, text))
            except Exception:
                pass

    if not all_msgs:
        print(f"Warning: no messages found (table: {table})")
        sys.exit(1)

    all_msgs.sort(key=lambda x: x[0])

    # ── Generate Markdown ──────────────────────────────────────────────────
    lines = [f"# 与{display_name}的微信聊天记录\n",
             f"共 {len(all_msgs)} 条消息\n\n---\n"]
    current_date = None
    for ts, is_me, text in all_msgs:
        dt = datetime.datetime.fromtimestamp(ts)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
        if date_str != current_date:
            current_date = date_str
            lines.append(f"\n## {date_str}\n")
        if is_me is None:
            # System message: no sender, italic
            lines.append(f"*{time_str} {text}*\n")
        else:
            sender = nick_map.get(my_wxid, "我") if is_me else display_name
            lines.append(f"**{time_str} {sender}**\n{text}\n")

    content = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    size_kb = os.path.getsize(out_path) // 1024
    print(f"Exported {len(all_msgs)} messages -> {out_path} ({size_kb} KB)")

# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WeChat chat history export tool")
    parser.add_argument("--name", help="Contact remark or nickname (fuzzy search)")
    parser.add_argument("--wxid", help="Specify wxid directly (skip search)")
    parser.add_argument("--group", action="store_true", help="Search group chats")
    parser.add_argument("--out", help="Output Markdown path (default: repo root)")
    parser.add_argument("--voice-json", help="Voice transcription JSON (from cache_voices.py), replaces [voice] placeholders")
    args = parser.parse_args()

    if not args.name and not args.wxid:
        parser.print_help()
        sys.exit(1)

    keys_file = find_keys_file()
    my_wxid, db_dir = detect_wxid_and_db_dir(keys_file)

    if args.wxid:
        target_wxid = args.wxid
        display_name = args.wxid
    else:
        results = search_contacts(keys_file, db_dir, args.name, group=args.group)
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

    # Default output path
    if args.out:
        out_path = os.path.expanduser(args.out)
    else:
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', display_name)
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_path = os.path.join(repo_dir, f"{safe_name}_聊天记录.md")

    # Load voice transcription map (optional)
    voice_map = None
    if args.voice_json:
        vj = os.path.expanduser(args.voice_json)
        if os.path.exists(vj):
            with open(vj, encoding="utf-8") as f:
                voice_map = json.load(f)
            print(f"Loaded voice transcriptions: {len(voice_map)} entries")
        else:
            print(f"Warning: voice-json file not found: {vj}")

    export_chat(target_wxid, display_name, keys_file, db_dir, out_path, voice_map=voice_map)

if __name__ == "__main__":
    main()
