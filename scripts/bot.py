"""
WeChat AI Bot - main entry point
Usage: python3.9 bot.py
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


# ── File watcher ─────────────────────────────────────────────────

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


# ── Trigger logic ────────────────────────────────────────────────

def should_trigger(raw: str, sender_id: int, at_me: bool) -> bool:
    if sender_id == 1:
        return '/xin' in raw
    return at_me or any(t in raw for t in BOT_TRIGGERS) or '/xin' in raw


# ── Message handling (thread pool) ───────────────────────────────

def handle_message(msg: tuple, history: list, table: str = None):
    import time as _t
    t0 = _t.time()
    try:
        text = extract_text(msg[3])
        print(f"[timing] Generating reply...", flush=True)
        import re as _re
        from config import MY_WXID
        ai_text = generate(history, text, table=table, my_wxid=MY_WXID, chat_wxid=next((wid for wid, t in zip(WATCH_IDS, WATCH_TABLES) if t==table), None))
        print(f"[timing] Claude took: {_t.time()-t0:.2f}s | raw: {repr(ai_text[:20])}", flush=True)
        # Strip any leading 👾 and whitespace that Claude may prepend
        ai_text = _re.sub(r'^[\s\U0001F47E]+', '', ai_text)
        reply = REPLY_PREFIX + ai_text
        print(f"[reply] {reply[:80]}", flush=True)
        t1 = _t.time()
        if send(reply):
            print(f"[timing] Send took: {_t.time()-t1:.2f}s | Total: {_t.time()-t0:.2f}s", flush=True)
    except Exception as e:
        print(f"[!] Error handling message: {e}", flush=True)
        traceback.print_exc()


# ── Main ─────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"  WeChat AI Bot | Watching {len(WATCH_IDS)} conversations")
    print("=" * 50)

    if not ANTHROPIC_API_KEY:
        print("[!] Missing ANTHROPIC_API_KEY")
        sys.exit(1)

    print("[*] Initializing contacts...")
    # Load direct-message contacts
    preload([wid for wid in WATCH_IDS if '@chatroom' not in wid])
    # Scan last 500 messages per conversation and cache every sender found
    for table in WATCH_TABLES:
        preload_from_messages(table)
    from core.contacts import _cache
    print(f"[*] Loaded {len(_cache)} contacts into cache")
    # Initialize per-conversation state: last_id, in-memory history, submitted set
    last_ids:   dict[str, int]   = {}
    history_cache: dict[str, deque] = {}
    submitted:  dict[str, set]   = {}

    for table in WATCH_TABLES:
        last_ids[table]      = get_max_id(table)
        history_cache[table] = deque(load_initial_history(table), maxlen=MAX_HISTORY)
        submitted[table]     = set()

    ids_lock = threading.Lock()
    print(f"[*] Ready, watching...\n")

    # Watch main DB and WAL file; any change triggers a check
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

            # Wait 50ms for batched events to arrive, then drain all
            time.sleep(0.05)
            while not change_queue.empty():
                change_queue.get_nowait()

            # Check each conversation for new messages
            for table in WATCH_TABLES:
                with ids_lock:
                    new_msgs = get_new_messages(table, last_ids[table])
                    if new_msgs:
                        last_ids[table] = max(m[0] for m in new_msgs)

                for msg in new_msgs:
                    # If deque is full, the oldest entry is about to be evicted -> vectorize in background
                    if len(history_cache[table]) >= MAX_HISTORY:
                        oldest = history_cache[table][0]
                        from core.embeddings import store as embed_store
                        from core.reader import extract_text as _et
                        from core.contacts import get_name as _gn
                        from core.ai import _extract_sender_wxid
                        old_text = _et(oldest[3])
                        old_wxid = _extract_sender_wxid(oldest[3])
                        old_sender = _gn(old_wxid) if old_wxid else ("Me" if oldest[2] == 1 else "?")
                        executor.submit(embed_store, table, oldest[0], old_text, old_sender, oldest[1])

                    # Append to in-memory cache (record regardless of whether trigger fires)
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

                    label = '@me' if at_me else ('self' if msg[2] == 1 else 'keyword')
                    print(f"[trigger/{label}] {text[:50]}", flush=True)

                    history_snapshot = list(history_cache[table])
                    executor.submit(handle_message, msg, history_snapshot, table)

    except KeyboardInterrupt:
        print('\n[*] Stopped')
    finally:
        observer.stop()
        observer.join()
        executor.shutdown(wait=False)


if __name__ == '__main__':
    main()
