#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
"""
export_chat.py — Export Weixin/WeChat chat history to Markdown (Windows)

Supports the xwechat_files database format used by the Windows Weixin app
(same schema as Mac WeChat: message_0.db, Msg_<md5> tables, contact.db).

Usage:
  python export_chat.py --name "John"
  python export_chat.py --name "SomeGroup" --group
  python export_chat.py --wxid wxid_xxxx
  python export_chat.py --name "John" --out C:/Desktop/output.md

Dependencies:
  pip install zstandard sqlcipher3-binary
"""
import argparse, hashlib, json, os, re, sys, tempfile, subprocess, datetime
import xml.etree.ElementTree as ET

try:
    import zstandard
except ImportError:
    sys.exit("Please install first: pip install zstandard")

EXPORT_TYPES_EXACT = (1, 3, 34, 43, 47, 50, 10000)


# ── Path helpers ──────────────────────────────────────────────────

def find_keys_file() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        candidate = os.path.join(here, "keys", "wechat_keys.json")
        if os.path.exists(candidate):
            return candidate
        here = os.path.dirname(here)
    sys.exit("Cannot find keys/wechat_keys.json. Run extract_key_windows.py first.")


def detect_wxid_and_db_dir(keys_file: str = None) -> tuple[str, str]:
    """Return (wxid, db_storage_dir).  db_storage_dir contains message/, contact/, ..."""
    import glob as _glob
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import XWECHAT_FILES

    if keys_file and os.path.exists(keys_file):
        keys = json.load(open(keys_file))
        for rel_path in keys:
            parts = rel_path.replace('\\', '/').split('/')
            if len(parts) >= 2:
                folder  = parts[0]
                wxid    = folder.split('_')[0]
                db_dir  = os.path.join(XWECHAT_FILES, folder, "db_storage")
                if os.path.isdir(db_dir):
                    return wxid, db_dir

    pattern = os.path.join(XWECHAT_FILES, "*", "db_storage", "message", "message_0.db")
    matches = _glob.glob(pattern)
    if not matches:
        sys.exit(f"No message_0.db found under {XWECHAT_FILES}")
    db_path = matches[0]
    folder  = db_path.replace(XWECHAT_FILES, '').lstrip('/\\').split(os.sep)[0]
    return folder.split('_')[0], os.path.join(XWECHAT_FILES, folder, "db_storage")


# ── SQLCipher query helper ────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import SQLCIPHER_BIN


def sqlcipher_query(db_path: str, key: str, sql: str) -> list[tuple]:
    if not os.path.exists(db_path):
        return []

    # Try sqlcipher3 (SHA512 first, then SHA1)
    try:
        import sqlcipher3 as _sc
        for sha1 in (False, True):
            try:
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
                pass
    except ImportError:
        pass

    # Binary fallback
    if not os.path.exists(SQLCIPHER_BIN):
        return []
    for sha1 in (False, True):
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
                               capture_output=True, text=True, timeout=15,
                               errors='replace')
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        rows = [tuple(line.split("|||")) for line in r.stdout.splitlines()
                if line and line != "ok"]
        if rows:
            return rows
    return []


# ── Contact search ────────────────────────────────────────────────

def search_contacts(keys_file: str, db_dir: str, name_query: str, group: bool = False):
    """Search contacts in contact.db, return [(wxid, display_name), ...]."""
    keys = json.load(open(keys_file))
    key  = next((v for k, v in keys.items()
                 if "contact/contact.db" in k.replace("\\", "/")), "")
    if not key:
        sys.exit("Cannot find contact.db key. Re-run extract_key_windows.py.")
    contact_db = os.path.join(db_dir, "contact", "contact.db")

    if group:
        where = (f"(nick_name LIKE '%{name_query}%' OR remark LIKE '%{name_query}%') "
                 f"AND username LIKE '%@chatroom%'")
    else:
        where = (f"(nick_name LIKE '%{name_query}%' OR remark LIKE '%{name_query}%') "
                 f"AND username NOT LIKE '%@chatroom%'")

    rows = sqlcipher_query(contact_db, key,
        f"SELECT username, nick_name, remark FROM contact WHERE {where} LIMIT 20;")
    results = []
    for row in rows:
        if len(row) < 3:
            continue
        wxid, nick, remark = row[0].strip(), row[1].strip(), row[2].strip()
        results.append((wxid, nick or wxid))  # use original WeChat nickname, not personal remark
    return results


def resolve_nickname(keys_file: str, db_dir: str, wxid: str) -> str:
    """Reverse-lookup a contact's WeChat nickname by wxid (not personal remark),
    so --wxid exports show the same display name as --name."""
    keys = json.load(open(keys_file))
    key  = next((v for k, v in keys.items()
                 if "contact/contact.db" in k.replace("\\", "/")), "")
    if not key:
        return ""
    contact_db = os.path.join(db_dir, "contact", "contact.db")
    rows = sqlcipher_query(contact_db, key,
        f"SELECT nick_name FROM contact WHERE username = '{wxid}' LIMIT 1;")
    if rows and rows[0] and rows[0][0]:
        return rows[0][0].strip()
    return ""


# ── Message decoding ──────────────────────────────────────────────

def _decode_raw(hex_str: str) -> str:
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
    if "\n" in text:
        first, rest = text.split("\n", 1)
        if re.match(r"^[\w]{4,30}:$", first.strip()):
            return rest.strip()
    return text.strip()


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


def _refermsg_meta(refermsg, nick_map=None) -> str:
    display = ""
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
    return " ".join(p for p in [display, timestr] if p)


def _format_refermsg(refermsg, prefix=">", nick_map=None) -> str:
    meta = _refermsg_meta(refermsg, nick_map)
    meta_line = f"{prefix} *{meta}*\n" if meta else ""
    content_el = refermsg.find("content")
    body, nested = "", ""
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
                    body = f"[语音 {max(1, round(int(vl)/1000))}s]" if vl else "[语音]"
                elif inner.find(".//videomsg") is not None:
                    pl = _attr_anywhere(inner, "playlength")
                    body = f"[视频 {int(pl)}s]" if pl else "[视频]"
                elif inner.find(".//img") is not None:
                    body = "[图片]"
                else:
                    body = "[消息]"
                inner_refer = inner.find(".//refermsg")
                if inner_refer is not None:
                    nested = _format_refermsg(inner_refer, prefix + ">", nick_map)
            else:
                body = "[消息]"
        else:
            body = raw
    if len(body) > 80:
        body = body[:80] + "…"
    if body:
        # body may contain newlines (the quoted message was multi-line);
        # every line needs the blockquote prefix or lines after the first
        # fall outside the quote block in rendered Markdown.
        quoted = "\n".join(f"{prefix} {ln}" for ln in body.split("\n"))
        result = meta_line + quoted
    else:
        result = meta_line.rstrip()
    if nested:
        result += "\n" + nested
    return result


_IMG_MD5_RE = re.compile(r'\bmd5="([a-f0-9]{32})"')
_IMG_ORIGSRC_RE = re.compile(r'\boriginsourcemd5="([a-f0-9]*)"')

def _xml_text(root, tag):
    if root is None: return ""
    el = root.find(f".//{tag}")
    if el is None or el.text is None: return ""
    return el.text.strip()


def _voip_status(msg: str, mtype: str) -> str:
    """Map a VoIP <msg> CDATA (+ <msg_type>) to a Chinese call status.
    Connected calls return the clock duration as-is (e.g. '08:12' / '01:12:15');
    every other outcome returns a state word. This account's client is English,
    so we match the English literals; Chinese-client equivalents are kept for
    forward-compat. Order matters: 'canceled by caller' (对方取消) must be tested
    before plain 'Canceled' (我方已取消)."""
    m = (msg or "").strip()
    dm = re.search(r"(?:Duration:|通话时长)\s*([0-9:]+)", m)
    if dm:
        return dm.group(1)
    return _voip_status_noncon(m, mtype)


def _clock_to_secs(clock: str) -> int:
    """'08:12' -> 492 ; '01:12:15' -> 4335 ; non-clock -> 0."""
    parts = clock.split(":")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return 0
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


def _voip_status_noncon(m: str, mtype: str) -> str:
    low = m.lower()
    if mtype == "101" or "answered elsewhere" in low or "其他设备" in m:
        return "对方在其他设备接听"
    if "canceled by caller" in low or "对方已取消" in m or "对方取消" in m:
        return "对方取消"
    if "wasn't answered" in low or "未接听" in m or "无人接听" in m:
        return "无人接听"
    if "declined" in low or "已拒绝" in m or "拒接" in m:
        return "对方拒接"
    if "line busy" in low or "占线" in m or "忙线" in m:
        return "占线未接"
    if low == "canceled" or m == "已取消":
        return "已取消"
    return m  # unknown outcome — surface the raw text rather than hide it

def format_msg(hex_str: str, local_type: int, voice_map: dict = None, ts: int = None,
               nick_map: dict = None, image_map: dict = None,
               is_me: bool = False):
    text = _decode_raw(hex_str)
    is_system = False
    # Anchor for harvest-back: ("img", md5) / ("voice", str(ts)) for editable lines,
    # else None. Read by export_chat right after this call (render order preserved).
    format_msg.anchor = None

    if local_type == 1:
        if not text or text.startswith("<"):
            return None, False
        return text, False
    elif local_type == 3:
        m = _IMG_MD5_RE.search(text or "")
        format_msg.anchor = ("img", m.group(1) if m else None)
        if image_map and m:
            entry = image_map.get("by_orig_md5", {}).get(m.group(1))
            if entry:
                thumb = entry.get("thumb")
                osrc = _IMG_ORIGSRC_RE.search(text or "")
                is_animated = bool(osrc and not osrc.group(1))
                kind = "动图" if is_animated else "图片"
                desc = (image_map.get("descriptions") or {}).get(m.group(1), "")
                tag = f"[{kind}: {desc}]" if desc else f"[{kind}]"
                if thumb:
                    # Image on its own line, description underneath — keeps
                    # the thumbnail and its (often long) description from
                    # crowding onto the same line.
                    return f"![](images/{thumb})\n{tag}", False
                return tag, False
        return "[图片]", False
    elif local_type == 34:
        format_msg.anchor = ("voice", str(ts)) if ts is not None else None
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
        return tag, False
    elif local_type == 43:
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
        root = _parse_xml(text)
        if root is not None:
            title_el = root.find(".//title")
            title    = (title_el.text or "").strip() if title_el is not None else ""
            des_el   = root.find(".//des")
            des      = (des_el.text or "").strip() if des_el is not None else ""
            type_el  = root.find(".//type")
            subtype  = int(type_el.text) if type_el is not None and type_el.text and type_el.text.strip().isdigit() else 0
            src_el   = root.find(".//sourcedisplayname")
            source   = (src_el.text or "").strip() if src_el is not None else ""

            refermsg = root.find(".//refermsg")
            if refermsg is not None:
                quoted = _format_refermsg(refermsg, ">", nick_map)
                if quoted and title:
                    return f"{title}\n{quoted}", False
                elif quoted:
                    return quoted, False
                elif title:
                    return f"[引用] {title}", False

            label = {5:"公众号",6:"文件",8:"表情",19:"聊天记录",33:"小程序",36:"小程序",
                     62:"互动",2000:"转账",2001:"红包"}.get(subtype, "链接")
            if subtype == 2000:
                # Author rule: I'm the subject, contact is the object.
                #   is_me  → just "[转账 ¥X]" (the act of transferring)
                #   !is_me → contact's REAL status, read straight from paysubtype.
                # A contact-side transfer row only exists once the contact has
                # acted on it, so each pst maps to a settled outcome (never a
                # "pending" guess): 3/8 收, 4 退回, 5 拒收. <des> is NOT
                # perspective-aware (legacy fallback string) — don't use it.
                amount = _xml_text(root, "feedesc")
                memo   = _xml_text(root, "pay_memo")
                pst_s  = _xml_text(root, "paysubtype")
                try:
                    pst = int(pst_s) if pst_s else -1
                except ValueError:
                    pst = -1
                if is_me:
                    label = "转账"
                else:
                    if pst in (3, 8):
                        label = "已收钱"
                    elif pst == 4:
                        label = "已退回"
                    elif pst == 5:
                        label = "已被拒收"
                    else:
                        label = "转账"
                parts = [label]
                if amount: parts.append(amount)
                if memo:   parts.append(f"备注:{memo}")
                return f"[{' '.join(parts)}]", False
            if subtype == 2001:
                memo = ""
                for tag_name in ("sendertitle", "pay_memo"):
                    el = root.find(f".//{tag_name}")
                    if el is not None and el.text and el.text.strip():
                        memo = el.text.strip(); break
                return (f"[红包 {memo}]" if memo else "[红包]"), False
            src_tag = f" | {source}" if source else ""
            des_tag = f" {des[:40]}" if des and not title else ""
            if title:
                return f"[{label}{src_tag}] {title}{des_tag}", False
            elif des:
                return f"[{label}{src_tag}] {des[:60]}", False
            return f"[{label}]", False
        return "[链接]", False
    elif local_type == 50:
        # WeChat 4.x VoIP bubble: <voipmsg><VoIPBubbleMsg> with CHILD elements
        # (the old code read them as attributes — they're elements, so it always
        # fell through to "[通话]"). Fields that matter:
        #   <msg> CDATA — the real outcome/duration text (English on this client)
        #   <room_type> — 1 = 语音通话, 0 = 视频通话 (the only media-kind signal)
        #   <msg_type>  — 100 normal, 101 = 多端"对方在其他设备接听"旁注
        #   <inviteid>  — 拨出时刻 (10-digit epoch when present)
        #   <duration>  — ALWAYS 0; never use it, talk time is inside <msg>
        root = _parse_xml(text)
        if root is not None and root.find(".//VoIPBubbleMsg") is not None:
            kind   = "视频通话" if _xml_text(root, "room_type") == "0" else "语音通话"
            status = _voip_status(_xml_text(root, "msg"), _xml_text(root, "msg_type"))
            body   = f"{kind} {status}" if status else kind
            # two timestamps: 起始 → 结果. 结果 = this msg's create_time (挂断/拒接/
            # 超时那一刻). 起始 prefers <inviteid> (真实拨出时刻, 10位 epoch); for a
            # connected call that didn't store a usable inviteid, derive it from
            # 结果 - 通话时长 (≈接通起点, within ring time of the real dial).
            out_t  = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            invite = _xml_text(root, "inviteid")
            start_epoch = None
            if invite.isdigit() and len(invite) == 10:
                start_epoch = int(invite)
            elif ts:
                secs = _clock_to_secs(status)   # nonzero only for connected calls
                if secs:
                    start_epoch = ts - secs
            dial_t = datetime.datetime.fromtimestamp(start_epoch).strftime("%H:%M:%S") if start_epoch else ""
            span   = f"{dial_t}→{out_t}" if (dial_t and out_t) else out_t
            if span:
                body = f"{body} · {span}"
            return f"[{body}]", False
        return "[通话]", False
    elif local_type == 10000:
        if text and not text.startswith("<"):
            return text, True
        if text and text.startswith("<"):
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


# ── Build nickname map ────────────────────────────────────────────

def _build_nick_map(keys_file: str, db_dir: str, my_wxid: str) -> dict:
    keys = json.load(open(keys_file))
    contact_key = next((v for k, v in keys.items()
                        if "contact/contact.db" in k.replace("\\", "/")), "")
    if not contact_key:
        return {}
    contact_db = os.path.join(db_dir, "contact", "contact.db")
    rows = sqlcipher_query(contact_db, contact_key, "SELECT username, nick_name FROM contact;")
    nick_map = {r[0].strip(): r[1].strip() for r in rows if len(r) >= 2 and r[0].strip() and r[1].strip()}
    if my_wxid not in nick_map:
        nick_map[my_wxid] = my_wxid
    return nick_map


# ── Core export ───────────────────────────────────────────────────

def export_chat(wxid: str, display_name: str, keys_file: str, db_dir: str,
                out_path: str, voice_map: dict = None, image_map: dict = None,
                anchors_path: str = None):
    keys  = json.load(open(keys_file))
    table = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()

    my_wxid, _ = detect_wxid_and_db_dir(keys_file)
    nick_map   = _build_nick_map(keys_file, db_dir, my_wxid)

    # All message_N.db files
    msg_db_dir = os.path.join(db_dir, "message")
    db_files   = sorted(
        [f for f in os.listdir(msg_db_dir) if re.match(r"message_\d+\.db$", f)],
        key=lambda x: int(re.search(r"\d+", x).group())
    )

    all_msgs: list[tuple] = []
    seen: set[int] = set()

    for db_name in db_files:
        db_path    = os.path.join(msg_db_dir, db_name)
        key_pat    = f"message/{db_name}"
        key        = next((v for k, v in keys.items()
                           if key_pat in k.replace("\\", "/")), "")
        if not key:
            continue

        has_table = sqlcipher_query(db_path, key,
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';")
        if not has_table:
            continue

        name2id = sqlcipher_query(db_path, key,
            "SELECT rowid, user_name FROM Name2Id ORDER BY rowid;")
        my_sender_ids = set()
        for row in name2id:
            if len(row) == 2:
                try:
                    if my_wxid in row[1].strip():
                        my_sender_ids.add(int(row[0]))
                except Exception:
                    pass

        type_list = ",".join(str(t) for t in EXPORT_TYPES_EXACT)
        rows = sqlcipher_query(db_path, key,
            f"SELECT create_time, real_sender_id, hex(message_content), local_type, server_id "
            f"FROM {table} WHERE local_type IN ({type_list}) "
            f"OR local_type % 65536 = 49 ORDER BY create_time ASC;")

        for row in rows:
            if len(row) < 5:
                continue
            try:
                ts        = int(row[0])
                sender_id = int(row[1])
                local_type = int(row[3])
                svr_id    = int(row[4]) if row[4] else 0
                is_me_flag = sender_id in my_sender_ids
                text, is_system = format_msg(row[2], local_type, voice_map=voice_map,
                                             ts=ts, nick_map=nick_map, image_map=image_map,
                                             is_me=is_me_flag)
                if text is None:
                    continue
                # Suppress WeChat's "update app" placeholder for unsupported message types
                if "does not support this content" in text or "Update to the latest version" in text:
                    continue
                if svr_id in seen:
                    continue
                seen.add(svr_id)
                is_me = None if is_system else is_me_flag
                anchor = getattr(format_msg, "anchor", None)
                all_msgs.append((ts, is_me, text, anchor))
            except Exception:
                pass

    if not all_msgs:
        print(f"Warning: no messages found (table: {table})")
        sys.exit(1)

    all_msgs.sort(key=lambda x: x[0])

    lines = [f"# 与{display_name}的微信聊天记录\n",
             f"共 {len(all_msgs)} 条消息\n\n---\n"]
    current_date = None
    emitted_anchors = []   # ordered {type,key} for each image/voice line, render order
    for ts, is_me, text, anchor in all_msgs:
        dt       = datetime.datetime.fromtimestamp(ts)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
        if date_str != current_date:
            current_date = date_str
            lines.append(f"\n## {date_str}\n")
        if is_me is None:
            lines.append(f"*{time_str} {text}*\n")
        else:
            sender = nick_map.get(my_wxid, "我") if is_me else display_name
            lines.append(f"**{time_str} {sender}**\n{text}\n")
        if anchor:
            emitted_anchors.append({"type": anchor[0], "key": anchor[1]})

    content = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Exported {len(all_msgs)} messages -> {out_path} ({os.path.getsize(out_path)//1024} KB)")
    if anchors_path:
        with open(anchors_path, "w", encoding="utf-8") as f:
            json.dump(emitted_anchors, f, ensure_ascii=False)
        print(f"Wrote {len(emitted_anchors)} anchors -> {anchors_path}")


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Weixin Windows chat history export")
    parser.add_argument("--name",       help="Contact remark or nickname (fuzzy)")
    parser.add_argument("--wxid",       help="Specify wxid directly")
    parser.add_argument("--group",      action="store_true", help="Search group chats")
    parser.add_argument("--out",        help="Output Markdown path")
    parser.add_argument("--voice-json", help="Voice transcription JSON")
    parser.add_argument("--image-index", help="image_index.json built by build_image_index.py")
    parser.add_argument("--image-descriptions", help="image_descriptions.json (md5 -> Chinese description)")
    parser.add_argument("--emit-anchors", help="write ordered {type,key} per image/voice line "
                                               "to this path (for harvest-back of manual edits)")
    args = parser.parse_args()

    if not args.name and not args.wxid:
        parser.print_help()
        sys.exit(1)

    keys_file        = find_keys_file()
    my_wxid, db_dir  = detect_wxid_and_db_dir(keys_file)

    if args.wxid:
        target_wxid = args.wxid
        display_name = resolve_nickname(keys_file, db_dir, target_wxid) or target_wxid
        print(f"Resolved: {display_name} ({target_wxid})")
    else:
        results = search_contacts(keys_file, db_dir, args.name, group=args.group)
        if not results:
            print(f"No contacts found matching '{args.name}'")
            sys.exit(1)
        if len(results) == 1:
            target_wxid, display_name = results[0]
            print(f"Found: {display_name} ({target_wxid})")
        else:
            print("Multiple contacts found:")
            for i, (w, n) in enumerate(results):
                print(f"  [{i+1}] {n} ({w})")
            choice = input("Enter number: ").strip()
            try:
                target_wxid, display_name = results[int(choice) - 1]
            except (ValueError, IndexError):
                sys.exit("Invalid selection")

    if args.out:
        out_path = os.path.expanduser(args.out)
    else:
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', display_name)
        repo_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_path  = os.path.join(repo_dir, f"{safe_name}_聊天记录.md")

    voice_map = None
    if args.voice_json:
        vj = os.path.expanduser(args.voice_json)
        if os.path.exists(vj):
            voice_map = json.load(open(vj, encoding="utf-8"))
            print(f"Loaded voice transcriptions: {len(voice_map)} entries")

    image_map = None
    if args.image_index:
        ij = os.path.expanduser(args.image_index)
        if os.path.exists(ij):
            image_map = json.load(open(ij, encoding="utf-8"))
            print(f"Loaded image index: {len(image_map.get('by_orig_md5', {}))} entries")
    if args.image_descriptions and image_map is not None:
        idsc = os.path.expanduser(args.image_descriptions)
        if os.path.exists(idsc):
            image_map["descriptions"] = json.load(open(idsc, encoding="utf-8"))
            print(f"Loaded image descriptions: {len(image_map['descriptions'])} entries")

    export_chat(target_wxid, display_name, keys_file, db_dir, out_path,
                voice_map=voice_map, image_map=image_map,
                anchors_path=os.path.expanduser(args.emit_anchors) if args.emit_anchors else None)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
