import json, os, sqlite3, time, threading
from . import settings

_lock = threading.Lock()
_db = None

def _conn():
    global _db
    if _db is None:
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        _db = sqlite3.connect(os.path.join(settings.DATA_DIR, "cache.db"), check_same_thread=False)
        _db.execute("CREATE TABLE IF NOT EXISTS cache (domain TEXT, step TEXT, ts REAL, payload TEXT, PRIMARY KEY(domain, step))")
        _db.commit()
    return _db

def get(domain, step):
    ttl = settings.CACHE_TTL_DAYS * 86400
    with _lock:
        row = _conn().execute("SELECT ts, payload FROM cache WHERE domain=? AND step=?", (domain, step)).fetchone()
    if not row or time.time() - row[0] > ttl:
        return None
    try:
        return json.loads(row[1])
    except ValueError:
        return None

def put(domain, step, payload):
    with _lock:
        c = _conn()
        c.execute("REPLACE INTO cache VALUES (?,?,?,?)", (domain, step, time.time(), json.dumps(payload, ensure_ascii=False)))
        c.commit()

def stats():
    with _lock:
        rows = _conn().execute("SELECT step, COUNT(*) FROM cache GROUP BY step").fetchall()
    return {s: n for s, n in rows}

def clear():
    with _lock:
        c = _conn()
        c.execute("DELETE FROM cache")
        c.commit()
