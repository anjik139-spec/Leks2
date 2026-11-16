import base64
import hashlib
import json
import random

import db
import gen
import providers
from config import DATA, OWNERS, WEBAPP_URL

COST = {
    "photo": 1,
    "video": 3,
    "song": 2,
    "doc": 1,
    "clip": 4,
    "edit": 1,
    "sticker": 1,
    "emoji": 1,
    "cover": 1,
    "ringtone": 1,
    "gif": 1,
    "batch": 0,
}
CAPS = {"free": 1, "lite": 2, "pro": 3, "studio": 6}


class Err(Exception):
    def __init__(self, code):
        self.code = code


def submit(uid, kind, p):
    if kind not in COST:
        raise Err("bad")
    u = db.user(uid)
    pro = db.is_pro(uid, u)
    tier = db.tier_of(uid, u)
    if db.busy(uid) >= CAPS[tier]:
        raise Err("busy")
    prompt = (p.get("prompt") or "").strip()
    if kind in ("photo", "video", "song", "clip", "cover") and not prompt and kind != "cover":
        raise Err("empty")
    if prompt and gen.MOD_RE.search(prompt):
        raise Err("moderation")
    cost = 0 if pro else COST[kind] + (1 if p.get("hq") else 0)
    if not pro and u["balance"] < cost:
        raise Err("nobalance")
    params = {
        k: p.get(k)
        for k in (
            "prompt",
            "style",
            "aspect",
            "hq",
            "dur",
            "motion",
            "tempo",
            "mood",
            "ref",
            "strength",
            "lyrics",
            "duet",
            "quality",
            "tpl",
            "fields",
            "csv",
            "work",
            "op",
            "mask",
            "photo",
            "song",
        )
        if p.get(k) is not None
    }
    if params.get("ref") and str(params["ref"]).startswith("data:image"):
        raw = base64.b64decode(str(params["ref"]).split(",", 1)[1])[:2_000_000]
        name = f"ref_{uid}_{db.now()}.png"
        gen.save(name, raw)
        params["ref"] = name
    if params.get("mask") and str(params["mask"]).startswith("data:image"):
        raw = base64.b64decode(str(params["mask"]).split(",", 1)[1])[:2_000_000]
        name = f"mask_{uid}_{db.now()}.png"
        gen.save(name, raw)
        params["mask"] = name
    params["watermark"] = tier == "free"
    key = hashlib.sha256(
        json.dumps(
            {k: v for k, v in params.items() if k not in ("ref", "mask")},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    hit = db.cache_get(key) or providers.sem_cache(kind, params)
    if hit and kind in ("photo", "video", "song"):
        jid = "c" + str(db.now() * 1000) + str(random.randint(10, 99))
        db.x(
            "INSERT INTO jobs(id,uid,kind,params,status,cost,files,ts,notify,priority) "
            "VALUES(?,?,?,?, 'done',0,?,?,1,?)",
            (
                jid,
                uid,
                kind,
                json.dumps(params, ensure_ascii=False),
                json.dumps(hit),
                db.now(),
                1 if pro else 0,
            ),
        )
        db.ev(uid, "cache_hit")
        return {"job": jid, "cached": True}
    if cost:
        db.upd(uid, balance=u["balance"] - cost)
    jid = "j" + str(db.now() * 1000) + str(random.randint(10, 99))
    db.x(
        "INSERT INTO jobs(id,uid,kind,params,cost,ts,priority) VALUES(?,?,?,?,?,?,?)",
        (
            jid,
            uid,
            kind,
            json.dumps(params, ensure_ascii=False),
            cost,
            db.now(),
            2 if tier == "studio" else 1 if pro else 0,
        ),
    )
    db.ev(uid, "job_" + kind)
    db.upd(uid, last_seen=db.now())
    return {"job": jid, "cached": False}


QDEFS = [
    ("d3", "Создай 3 работы сегодня", 3, "gen_today"),
    ("w10", "10 генераций за неделю", 10, "gen_week"),
    ("like3", "Получи 3 лайка", 3, "likes"),
    ("ref1", "Пригласи друга", 1, "ref"),
    ("clip1", "Собери клип", 1, "clip"),
    ("doc2", "2 документа", 2, "doc"),
]


def quest_progress(uid, metric):
    t = db.now()
    if metric == "gen_today":
        return db.q(
            "SELECT COUNT(*) c FROM jobs WHERE uid=? AND status='done' AND ts>?",
            (uid, t - 86400),
        )[0]["c"]
    if metric == "gen_week":
        return db.q(
            "SELECT COUNT(*) c FROM jobs WHERE uid=? AND status='done' AND ts>?",
            (uid, t - 7 * 86400),
        )[0]["c"]
    if metric == "likes":
        return db.q(
            "SELECT COUNT(*) c FROM likes l JOIN feed f ON f.work=l.work WHERE f.uid=?",
            (uid,),
        )[0]["c"]
    if metric == "ref":
        return db.user(uid)["ref_count"]
    if metric == "clip":
        return db.q(
            "SELECT COUNT(*) c FROM jobs WHERE uid=? AND kind='clip' AND status='done'",
            (uid,),
        )[0]["c"]
    if metric == "doc":
        return db.q(
            "SELECT COUNT(*) c FROM jobs WHERE uid=? AND kind='doc' AND status='done'",
            (uid,),
        )[0]["c"]
    return 0
