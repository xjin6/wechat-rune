"""keystore.py — append-only, multi-candidate key store (never lose a key).

Two files in scripts/keys/:
  wechat_keys_pool.json   {db_rel_path: [all unique keys ever seen]}   APPEND-ONLY
  wechat_keys.json        {db_rel_path: the one key that currently decrypts}  (what
                          every consumer reads — format unchanged, single key/DB)

Why: capture runs are partial and one bad overwrite once wiped 18 keys down to 2.
Here keys only ever accumulate in the pool; wechat_keys.json is *re-resolved* from
the pool by HMAC-validating each candidate against the DB's page-1 and keeping the
one that actually decrypts. If a DB is ever re-keyed, old + new keys both live in
the pool and resolve() automatically picks whichever is valid now.

CLI:
  python keystore.py seed     # fold current wechat_keys.json into the pool
  python keystore.py resolve  # re-pick valid keys from the pool -> wechat_keys.json
  python keystore.py show     # print pool sizes + resolved/validated status
Programmatic: keystore.add({rel: key, ...})  # append to pool + re-resolve
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_key_windows as ekw

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(HERE, "wechat_keys_pool.json")
RESOLVED_PATH = os.path.join(HERE, "wechat_keys.json")


def _load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(p, d):
    json.dump(d, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def _page1_map():
    db_files, _ = ekw.collect_db_files()
    return {rel: p1 for rel, path, sz, salt, p1 in db_files}


def add_to_pool(found: dict) -> dict:
    """Append {rel: key} into the pool (dedup, never removes). Returns the pool."""
    pool = _load(POOL_PATH)
    for rel, key in (found or {}).items():
        lst = pool.setdefault(rel, [])
        if key not in lst:
            lst.append(key)
    _save(POOL_PATH, pool)
    return pool


def resolve() -> dict:
    """Pick, per DB, the pooled candidate that HMAC-validates against its page-1,
    and write that to wechat_keys.json. DBs not on disk keep their existing resolved
    value (or the first candidate). Never drops a working resolved key."""
    pool = _load(POOL_PATH)
    resolved = _load(RESOLVED_PATH)
    page1 = _page1_map()
    for rel, candidates in pool.items():
        if rel in page1:
            valid = next((k for k in candidates if ekw.verify_key(k, page1[rel])), None)
            if valid:
                resolved[rel] = valid
            elif rel not in resolved and candidates:
                resolved[rel] = candidates[-1]  # best-effort: newest candidate
        else:
            if rel not in resolved and candidates:
                resolved[rel] = candidates[-1]
    _save(RESOLVED_PATH, resolved)
    return resolved


def add(found: dict) -> dict:
    """Append new keys to the pool, then re-resolve wechat_keys.json. The canonical
    write path for every capturer — keys only ever accumulate."""
    add_to_pool(found)
    return resolve()


def seed():
    """Fold the current wechat_keys.json into the pool (one-time / idempotent)."""
    add_to_pool(_load(RESOLVED_PATH))


def _show():
    pool = _load(POOL_PATH)
    page1 = _page1_map()
    print(f"pool: {len(pool)} DBs")
    for rel in sorted(pool):
        cands = pool[rel]
        status = ""
        if rel in page1:
            valid = [i for i, k in enumerate(cands) if ekw.verify_key(k, page1[rel])]
            status = f"valid#{valid}" if valid else "NONE valid"
        print(f"  {os.path.basename(rel.replace(chr(92),'/')):28} {len(cands)} cand(s) {status}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "seed":
        seed(); print(f"seeded pool from wechat_keys.json -> {len(_load(POOL_PATH))} DBs")
    elif cmd == "resolve":
        r = resolve(); print(f"resolved {len(r)} keys -> wechat_keys.json")
    else:
        _show()
