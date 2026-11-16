import hashlib
import json
import os
import sqlite3
import threading
import time

from config import DATA, OWNERS

os.makedirs(DATA, exist_ok=True)
os.makedirs(DATA + "/files", exist_ok=True)
C = sqlite3.connect(DATA + "/bot.db", check_same_thread=False)
C.row_factory = sqlite3.Row
C.execute("PRAGMA journal_mode=WAL")
L = threading.Lock()


def q(s, p=()):
    with L:
        return [dict(r) for r in C.execute(s, p).fetchall()]


def x(s, p=()):
    with L:
        cur = C.execute(s, p)
        C.commit()
        return cur.lastrowid


def now():
    return int(time.time())


C.executescript(
    """
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        name TEXT DEFAULT '',
        balance INTEGER DEFAULT 5,
        pro_until INTEGER DEFAULT 0,
        sub INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0,
        last_bonus INTEGER DEFAULT 0,
        ref_by INTEGER DEFAULT 0,
        ref_count INTEGER DEFAULT 0,
        ref_paid INTEGER DEFAULT 0,
        favs TEXT DEFAULT '[]',
        created INTEGER
    );
    CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY,
        uid INTEGER,
        kind TEXT,
        params TEXT,
        status TEXT DEFAULT 'pending',
        tries INTEGER DEFAULT 0,
        run_at INTEGER DEFAULT 0,
        cost INTEGER DEFAULT 0,
        files TEXT DEFAULT '[]',
        error TEXT DEFAULT '',
        notify INTEGER DEFAULT 0,
        send INTEGER DEFAULT 0,
        ts INTEGER
    );
    CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,files TEXT,ts INTEGER);
    CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY,kind TEXT,value INTEGER,max INTEGER,used INTEGER DEFAULT 0,exp INTEGER);
    CREATE TABLE IF NOT EXISTS payments(uid INTEGER,payload TEXT,amount INTEGER,ts INTEGER);
    CREATE TABLE IF NOT EXISTS events(uid INTEGER,name TEXT,ts INTEGER);
    CREATE TABLE IF NOT EXISTS likes(uid INTEGER,work TEXT,ts INTEGER,PRIMARY KEY(uid,work));
    CREATE TABLE IF NOT EXISTS feed(work TEXT PRIMARY KEY,uid INTEGER,ts INTEGER,caption TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS flags(key TEXT PRIMARY KEY,val TEXT,pct INTEGER DEFAULT 100);
    CREATE TABLE IF NOT EXISTS spend(id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT,cost REAL,ts INTEGER);
    CREATE TABLE IF NOT EXISTS init_sessions(hash TEXT PRIMARY KEY,uid INTEGER,ts INTEGER);
    CREATE TABLE IF NOT EXISTS quests(uid INTEGER,key TEXT,ts INTEGER,PRIMARY KEY(uid,key));
    CREATE TABLE IF NOT EXISTS rag(uid INTEGER,chunk TEXT,ts INTEGER);
    CREATE INDEX IF NOT EXISTS j_status ON jobs(status,run_at);
    CREATE INDEX IF NOT EXISTS j_uid ON jobs(uid,ts);
    """
)

MIG = [
    "ALTER TABLE users ADD COLUMN xp INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN skin TEXT DEFAULT 'mint'",
    "ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'",
    "ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN api_key TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN doc_profile TEXT DEFAULT '{}'",
    "ALTER TABLE users ADD COLUMN last_seen INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN write_ok INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN winback INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN reminded INTEGER DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN priority INTEGER DEFAULT 0",
    "ALTER TABLE cache ADD COLUMN emb TEXT DEFAULT ''",
    "ALTER TABLE cache ADD COLUMN kind TEXT DEFAULT ''",
]
for migration in MIG:
    try:
        x(migration)
    except Exception:
        pass


def user(uid, name=""):
    r = q("SELECT * FROM users WHERE id=?", (uid,))
    if r:
        if name and r[0]["name"] != name:
            x("UPDATE users SET name=? WHERE id=?", (name, uid))
        return r[0]
    x(
        "INSERT INTO users(id,name,created,last_seen) VALUES(?,?,?,?)",
        (uid, name, now(), now()),
    )
    return q("SELECT * FROM users WHERE id=?", (uid,))[0]


def upd(uid, **kw):
    x(
        "UPDATE users SET " + ",".join(k + "=?" for k in kw) + " WHERE id=?",
        (*kw.values(), uid),
    )
    return user(uid)


def is_pro(uid, u=None):
    u = u or user(uid)
    return uid in OWNERS or u["pro_until"] > now()


def tier_of(uid, u=None):
    u = u or user(uid)
    if uid in OWNERS:
        return "studio"
    return u["tier"] if u["pro_until"] > now() else "free"


def add_xp(uid, n):
    u = user(uid)
    xp = u["xp"] + n
    level = int((xp / 100) ** 0.5) + 1
    upd(uid, xp=xp)
    return level


def favs(uid):
    return json.loads(user(uid)["favs"] or "[]")


def set_favs(uid, f):
    upd(uid, favs=json.dumps(f))


def cache_get(k):
    r = q("SELECT files FROM cache WHERE key=?", (k,))
    return json.loads(r[0]["files"]) if r else None


def cache_set(k, f, emb="", kind=""):
    x(
        "INSERT OR REPLACE INTO cache(key,files,ts,emb,kind) VALUES(?,?,?,?,?)",
        (k, json.dumps(f), now(), json.dumps(emb), kind),
    )


def claim():
    with L:
        r = C.execute(
            "UPDATE jobs SET status='running',tries=tries+1 "
            "WHERE id=(SELECT id FROM jobs WHERE status='pending' AND run_at<=? "
            "ORDER BY priority DESC,ts LIMIT 1) RETURNING *",
            (now(),),
        ).fetchone()
        C.commit()
        return dict(r) if r else None


def retry(j, err):
    if j["tries"] < 3:
        x(
            "UPDATE jobs SET status='pending',run_at=?,error=? WHERE id=?",
            (now() + 5 * j["tries"], err[:300], j["id"]),
        )
    else:
        x(
            "UPDATE jobs SET status='error',error=? WHERE id=?",
            (err[:300], j["id"]),
        )
        if j["cost"]:
            x(
                "UPDATE users SET balance=balance+? WHERE id=?",
                (j["cost"], j["uid"]),
            )


def busy(uid):
    return q(
        "SELECT COUNT(*) c FROM jobs WHERE uid=? AND status IN ('pending','running')",
        (uid,),
    )[0]["c"]


def ev(uid, name):
    x("INSERT INTO events VALUES(?,?,?)", (uid, name, now()))


def spend_add(p, c):
    x("INSERT INTO spend(provider,cost,ts) VALUES(?,?,?)", (p, c, now()))


def spend_today():
    return q(
        "SELECT COALESCE(SUM(cost),0) s FROM spend WHERE ts>?", (now() - 86400,)
    )[0]["s"]


def flags_for(uid):
    out = {}
    for flag in q("SELECT * FROM flags"):
        if (
            flag["pct"] >= 100
            or int(
                hashlib.sha256(f"{uid}:{flag['key']}".encode()).hexdigest()[:4], 16
            )
            % 100
            < flag["pct"]
        ):
            out[flag["key"]] = flag["val"]
    return out


def like_toggle(uid, w):
    if q("SELECT 1 FROM likes WHERE uid=? AND work=?", (uid, w)):
        x("DELETE FROM likes WHERE uid=? AND work=?", (uid, w))
        return False
    x("INSERT INTO likes VALUES(?,?,?)", (uid, w, now()))
    return True


def likes_of(w):
    return q("SELECT COUNT(*) c FROM likes WHERE work=?", (w,))[0]["c"]
