"""import_keys.py — 从外部工具导出的密钥文件导入并验证，兼容写入 wechat_keys.json。

用法:
  python scripts/keys/import_keys.py <外部密钥文件>

为什么能兼容任意格式：
  不解析外部文件的具体结构，而是用正则提取文件中**所有** 64 位十六进制候选
  密钥（以及 96 位 key+salt 连写、字节分隔/0x 前缀等形式），再用每个本地数据库
  的第一页 (page1) 做 SQLCipher HMAC 验证来确定每个密钥归属哪个库。验证不通过的
  候选一律丢弃，所以：
    - 兼容任何导出工具（PyWxDump / SharpWxDump / wechat-dump-rs / 纯 hex 列表…）
    - 绝对安全：别的账号或无关字符串绝不会被误采用
  匹配成功的 {相对路径: 密钥} 通过 merge_write 合并进 wechat_keys.json（不覆盖）。
"""
import os
import sys
import re
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_key_windows import collect_db_files, verify_key, merge_write


def extract_candidates(text: str) -> list:
    """提取所有唯一 64-hex 候选密钥（保序）。

    兼容多种导出格式（最终都靠 page1 验证归属，假候选会被安全丢弃）：
      - 纯 64-hex 行
      - "路径: 64hex" / "路径 = 64hex" / "wxid<tab>64hex" / JSON 值
      - 96-hex（key+salt 连写）→ 取前 64
      - 字节分隔形式：ab:cd:ef.. / ab-cd-.. / 0xAB,0xCD.. / 空格分隔
    """
    seen = set()
    cands = []

    def add(h: str):
        h = h.lower()
        if len(h) >= 64:
            h = h[:64]
        if len(h) == 64 and all(c in '0123456789abcdef' for c in h) and h not in seen:
            seen.add(h)
            cands.append(h)

    # 1) 连续 96-hex（key+salt）取前 64
    for m in re.finditer(r'[0-9a-fA-F]{96}', text):
        add(m.group(0)[:64])
    # 2) 连续 64-hex
    for m in re.finditer(r'[0-9a-fA-F]{64}', text):
        add(m.group(0))
    # 3) 逐行兜底：去掉 0x 前缀与所有非 hex 分隔符后再找 64-hex
    #    覆盖 "ab:cd:.." / "0xAB 0xCD .." / 空格分隔字节等形式
    for line in text.splitlines():
        stripped = re.sub(r'0[xX]', '', line)
        stripped = re.sub(r'[^0-9a-fA-F]', '', stripped)
        for m in re.finditer(r'[0-9a-fA-F]{64}', stripped):
            add(m.group(0))
    return cands


def main():
    if len(sys.argv) < 2:
        print("Usage: import_keys.py <key_file>")
        sys.exit(2)
    src = sys.argv[1]
    if not os.path.exists(src):
        print(f"[!] File not found: {src}")
        sys.exit(1)

    raw = open(src, 'rb').read()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('latin-1', 'replace')

    cands = extract_candidates(text)
    print(f"External file : {src}")
    print(f"Candidates    : {len(cands)} unique 64-hex key(s)")

    db_files, salt_map = collect_db_files()
    print(f"Local DBs     : {len(db_files)}")

    found = {}
    for rel, path, sz, salt, page1 in db_files:
        if rel in found:
            continue
        for c in cands:
            if verify_key(c, page1):
                found[rel] = c
                break

    out_path, result, new, updated = merge_write(found)
    print(f"Matched       : {len(found)} DB key(s) "
          f"({new} new, {updated} updated); merged total {len(result)}")
    print(f"Saved         : {out_path}")

    # 核心库齐全性检查（导出/转录所需）
    have = set(os.path.basename(r) for r in result)
    core = []
    for b in ("message_0.db", "message_1.db", "contact.db", "media_0.db"):
        core.append(f"{b}={'OK' if b in have else 'MISSING'}")
    print("Core DBs      : " + ", ".join(core))

    for rel in sorted(found):
        print(f"  matched: {rel}")

    # 写一份机器可读摘要，便于无回显环境下复核
    summary = {
        "source": src,
        "candidates": len(cands),
        "matched": len(found),
        "merged_total": len(result),
        "core": {b: (b in have) for b in
                 ("message_0.db", "message_1.db", "contact.db", "media_0.db")},
        "matched_rels": sorted(found.keys()),
    }
    sp = os.path.join(os.path.dirname(__file__), "import_keys_result.json")
    with open(sp, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Summary       : {sp}")


if __name__ == '__main__':
    main()
