"""
微信AI机器人 - 主入口
用法：python3.9 bot.py
"""

import os, sys, time, traceback, threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    WATCH_TABLES, WATCH_IDS, BOT_TRIGGERS,
    REPLY_PREFIX, ANTHROPIC_API_KEY, WECHAT_DB_PATH, WECHAT_WAL_PATH, MAX_HISTORY
)
from core.decrypt import db_mtime
from core.reader import (
    get_max_id, get_new_messages, load_initial_history,
    extract_text, decode_raw, is_at_me
)

from core.sender import send
from core.ai import generate
from core.contacts import preload, preload_from_messages, get_name


# ── 文件监听 ─────────────────────────────────────────────────────

class DBWatcher(FileSystemEventHandler):
    def __init__(self, queue: Queue, db_name: str):
        self.queue = queue
        self.db_name = db_name  # e.g. "message_0.db"

    def on_modified(self, event):
        name = os.path.basename(event.src_path)
        if name == self.db_name or name == self.db_name + '-wal':
            import time as _t
            print(f"[FSEvent] {name} @ {_t.strftime('%H:%M:%S')}", flush=True)
            self.queue.put(True)


# ── 触发判断 ──────────────────────────────────────────────────────

def should_trigger(raw: str, sender_id: int, at_me: bool) -> bool:
    if sender_id == 1:
        return '/xin' in raw
    return at_me or any(t in raw for t in BOT_TRIGGERS) or '/xin' in raw


# ── 消息处理（线程池）────────────────────────────────────────────

def handle_message(msg: tuple, history: list, table: str = None):
    import time as _t
    t0 = _t.time()
    try:
        text = extract_text(msg[3])
        print(f"[timing] 开始生成回复...", flush=True)
        import re as _re
        from config import MY_WXID
        ai_text = generate(history, text, table=table, my_wxid=MY_WXID)
        print(f"[timing] Claude耗时: {_t.time()-t0:.2f}s | raw: {repr(ai_text[:20])}", flush=True)
        # 删掉Claude开头可能自带的所有👾和空白
        ai_text = _re.sub(r'^[\s\U0001F47E]+', '', ai_text)
        reply = REPLY_PREFIX + ai_text
        print(f"[回复] {reply[:80]}", flush=True)
        t1 = _t.time()
        if send(reply):
            print(f"[timing] 发送耗时: {_t.time()-t1:.2f}s | 总耗时: {_t.time()-t0:.2f}s", flush=True)
    except Exception as e:
        print(f"[!] 处理出错: {e}", flush=True)
        traceback.print_exc()


# ── 主函数 ────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"  微信AI机器人 | 监听 {len(WATCH_IDS)} 个对话")
    print("=" * 50)

    if not ANTHROPIC_API_KEY:
        print("[!] 缺少 ANTHROPIC_API_KEY")
        sys.exit(1)

    print("[*] 初始化联系人...")
    # 私聊对象直接加载
    preload([wid for wid in WATCH_IDS if '@chatroom' not in wid])
    # 每个对话扫描最近500条消息，把里面出现过的人全部加载进缓存
    for table in WATCH_TABLES:
        preload_from_messages(table)
    from core.contacts import _cache
    print(f"[*] 已加载 {len(_cache)} 个联系人到缓存")
    # 初始化每个对话的：last_id、内存历史缓存、已提交集合
    last_ids:   dict[str, int]   = {}
    history_cache: dict[str, deque] = {}
    submitted:  dict[str, set]   = {}

    for table in WATCH_TABLES:
        last_ids[table]      = get_max_id(table)
        history_cache[table] = deque(load_initial_history(table), maxlen=MAX_HISTORY)
        submitted[table]     = set()

    ids_lock = threading.Lock()
    print(f"[*] 就绪，监听中...\n")

    # 监听主DB和WAL文件，任一变化即触发
    change_queue: Queue = Queue()
    db_name = os.path.basename(WECHAT_DB_PATH)
    observer = Observer()
    observer.schedule(DBWatcher(change_queue, db_name),
                      os.path.dirname(WECHAT_DB_PATH), recursive=False)
    observer.start()

    executor = ThreadPoolExecutor(max_workers=4)
    now = time.time

    try:
        while True:
            try:
                change_queue.get(timeout=1.0)
            except Empty:
                continue

            # 等50ms让同批事件到齐，全部drain
            time.sleep(0.05)
            while not change_queue.empty():
                change_queue.get_nowait()

            # 检查各对话新消息
            for table in WATCH_TABLES:
                with ids_lock:
                    new_msgs = get_new_messages(table, last_ids[table])
                    if new_msgs:
                        last_ids[table] = max(m[0] for m in new_msgs)

                for msg in new_msgs:
                    # 追加到内存缓存（不管是否触发都记录）
                    history_cache[table].append(msg)

                    if now() - msg[1] > 30:
                        continue

                    raw  = decode_raw(msg[3])
                    text = extract_text(msg[3])
                    if not text or text.startswith('<'):
                        continue

                    at_me = is_at_me(msg[4])
                    if not should_trigger(raw, msg[2], at_me):
                        continue

                    if msg[0] in submitted[table]:
                        continue
                    submitted[table].add(msg[0])

                    label = '@我' if at_me else ('自己' if msg[2] == 1 else '关键词')
                    print(f"[触发/{label}] {text[:50]}", flush=True)

                    history_snapshot = list(history_cache[table])
                    executor.submit(handle_message, msg, history_snapshot, table)

    except KeyboardInterrupt:
        print('\n[*] 停止')
    finally:
        observer.stop()
        observer.join()
        executor.shutdown(wait=False)


if __name__ == '__main__':
    main()
