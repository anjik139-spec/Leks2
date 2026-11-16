import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import random
import smtplib
import time
import urllib.parse
import zipfile
from email.message import EmailMessage

from aiohttp import web

import core
import db
import gen
import providers
import worker
from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    DATA,
    INIT_TTL,
    MAIL_FROM,
    OWNERS,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_USER,
    WEBAPP_URL,
)

PLANS = {
    "lite": (100, 30, True),
    "month": (250, 30, True),
    "studio": (600, 30, True),
    "year": (1990, 365, True),
    "once_month": (250, 30, False),
}
PACKS = {"p10": (10, 50), "p50": (50, 200), "p200": (200, 700)}
BUCKETS = {}
START = time.time()
PROXY = os.getenv("PROXY_URL", "")


def rl(uid, cap, per):
    t = time.time()
    b = BUCKETS.setdefault(uid, [cap, t])
    b[0] = min(cap, b[0] + (t - b[1]) * cap / per)
    b[1] = t
    if b[0] < 1:
        return False
    b[0] -= 1
    return True


def check_init(s):
    try:
        p = dict(urllib.parse.parse_qsl(s, keep_blank_values=True))
        h = p.pop("hash", "")
        if not h or time.time() - int(p.get("auth_date", 0)) > INIT_TTL:
            return None
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(p.items()))
        sec = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if hmac.new(sec, dcs.encode(), hashlib.sha256).hexdigest() != h:
            return None
        u = json.loads(p.get("user", "{}"))
        row = db.q("SELECT uid FROM init_sessions WHERE hash=?", (h,))
        if row and row[0]["uid"] != u["id"]:
            return None
        db.x(
            "INSERT OR REPLACE INTO init_sessions VALUES(?,?,?)",
            (h, u["id"], db.now()),
        )
        return u
    except Exception:
        return None


async def auth(req):
    u = check_init(req.headers.get("X-Telegram-Init-Data", ""))
    if not u:
        raise web.HTTPUnauthorized(
            text='{"code":"auth"}', content_type="application/json"
        )
    if not rl("g" + str(u["id"]), 90, 60):
        raise web.HTTPTooManyRequests(
            text='{"code":"rate"}', content_type="application/json"
        )
    db.user(u["id"], u.get("first_name", ""))
    db.upd(u["id"], last_seen=db.now())
    return u


def jerr(code, status=400):
    raise web.HTTPException(
        text=json.dumps({"code": code}),
        content_type="application/json",
        status=status,
    )


def surl(name, uid, ttl=7200):
    e = db.now() + ttl
    s = hmac.new(
        BOT_TOKEN.encode(), f"{name}:{uid}:{e}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"/files/{name}?u={uid}&e={e}&s={s}"


def sign_files(fs, uid):
    return [dict(f, url=surl(f["name"], uid)) for f in fs]


@web.middleware
async def sec(req, handler):
    resp = await handler(req)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob: https:; "
        "media-src 'self' blob:; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline' https://telegram.org; "
        "connect-src 'self' https://telegram.org; object-src 'self'; "
        "frame-ancestors 'self' https://web.telegram.org"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


async def index(req):
    return web.FileResponse("webapp/index.html")


async def health(req):
    return web.json_response({"ok": True})


async def metrics(req):
    t = db.now()
    txt = f"""# TYPE lex_users gauge
lex_users {db.q('SELECT COUNT(*) c FROM users')[0]['c']}
lex_pro {db.q('SELECT COUNT(*) c FROM users WHERE pro_until>?', (t,))[0]['c']}
lex_jobs_done {db.q("SELECT COUNT(*) c FROM jobs WHERE status='done'")[0]['c']}
lex_jobs_error {db.q("SELECT COUNT(*) c FROM jobs WHERE status='error'")[0]['c']}
lex_queue {db.q("SELECT COUNT(*) c FROM jobs WHERE status='pending'")[0]['c']}
lex_revenue_stars {db.q('SELECT COALESCE(SUM(amount),0) s FROM payments')[0]['s']}
lex_spend_usd {db.spend_today()}
lex_uptime {int(time.time() - START)}
"""
    return web.Response(text=txt, content_type="text/plain")


async def state(req):
    u = await auth(req)
    d = db.user(u["id"])
    pro = db.is_pro(u["id"], d)
    tier = db.tier_of(u["id"], d)
    jobs = db.q(
        "SELECT * FROM jobs WHERE uid=? AND status='done' "
        "AND kind NOT IN ('broadcast') ORDER BY ts DESC LIMIT 60",
        (u["id"],),
    )
    favs = db.favs(u["id"])
    pub = {r["work"] for r in db.q("SELECT work FROM feed WHERE uid=?", (u["id"],))}
    gallery = []
    for j in jobs:
        p = json.loads(j["params"])
        if p.get("deleted"):
            continue
        gallery.append(
            {
                "id": j["id"],
                "kind": j["kind"],
                "params": p,
                "files": sign_files(json.loads(j["files"]), u["id"]),
                "ts": j["ts"],
                "fav": j["id"] in favs,
                "published": j["id"] in pub,
            }
        )
    stats = {
        k: db.q(
            "SELECT COUNT(*) c FROM jobs WHERE uid=? AND kind=? AND status='done'",
            (u["id"], k),
        )[0]["c"]
        for k in ("photo", "video", "song", "doc")
    }
    stats["gen"] = sum(stats.values())
    return web.json_response(
        {
            "user": {
                "id": u["id"],
                "name": d["name"],
                "owner": u["id"] in OWNERS,
                "admin": u["id"] in OWNERS,
                "tier": tier,
                "level": int((d["xp"] / 100) ** 0.5) + 1,
                "xp": d["xp"],
                "skin": d["skin"],
                "write_ok": bool(d["write_ok"]),
            },
            "pro": pro,
            "pro_until": d["pro_until"],
            "sub": bool(d["sub"]),
            "balance": d["balance"],
            "streak": d["streak"],
            "can_bonus": d["last_bonus"] < db.now() - 86400,
            "ref_count": d["ref_count"],
            "stats": stats,
            "gallery": gallery,
            "trial_used": bool(d["trial_used"]),
            "api_key": d["api_key"],
            "doc_profile": json.loads(d["doc_profile"] or "{}"),
            "flags": db.flags_for(u["id"]),
            "season": db.flags_for(u["id"]).get("season", ""),
            "doctpls": [
                {
                    "id": k,
                    "title": v["title"],
                    "fields": [
                        {"id": a, "label": b} for a, b in v["fields"]
                    ],
                }
                for k, v in gen.DOCTPL.items()
            ],
        }
    )


async def jobs(req):
    u = await auth(req)
    p = await req.json()
    if not rl("gen" + str(u["id"]), 8, 60):
        jerr("rate", 429)
    try:
        r = core.submit(u["id"], p.get("mode"), p)
    except core.Err as e:
        jerr(
            e.code,
            402
            if e.code == "nobalance"
            else 429
            if e.code in ("rate", "busy")
            else 400,
        )
    return web.json_response(r)


async def job_get(req):
    u = await auth(req)
    j = db.q("SELECT * FROM jobs WHERE id=?", (req.match_info["id"],))
    if not j or j[0]["uid"] != u["id"]:
        jerr("notfound", 404)
    j = j[0]
    return web.json_response(
        {
            "status": j["status"],
            "files": sign_files(json.loads(j["files"]), u["id"]),
            "error": j["error"],
            "tries": j["tries"],
        }
    )


async def fav(req):
    u = await auth(req)
    jid = (await req.json()).get("id")
    f = db.favs(u["id"])
    f = [i for i in f if i != jid] if jid in f else f + [jid]
    db.set_favs(u["id"], f)
    return web.json_response({"fav": jid in f})


async def delete(req):
    u = await auth(req)
    jid = (await req.json()).get("id")
    j = db.q(
        "SELECT params FROM jobs WHERE id=? AND uid=?", (jid, u["id"])
    )
    if j:
        p = json.loads(j[0]["params"])
        p["deleted"] = True
        db.x(
            "UPDATE jobs SET params=? WHERE id=?",
            (json.dumps(p, ensure_ascii=False), jid),
        )
    return web.json_response({"ok": True})


async def restore(req):
    u = await auth(req)
    jid = (await req.json()).get("id")
    j = db.q(
        "SELECT params FROM jobs WHERE id=? AND uid=?", (jid, u["id"])
    )
    if j:
        p = json.loads(j[0]["params"])
        p.pop("deleted", None)
        db.x(
            "UPDATE jobs SET params=? WHERE id=?",
            (json.dumps(p, ensure_ascii=False), jid),
        )
    return web.json_response({"ok": True})


async def send(req):
    u = await auth(req)
    jid = (await req.json()).get("id")
    db.x(
        "UPDATE jobs SET send=1,notify=0 WHERE id=? AND uid=? AND status='done'",
        (jid, u["id"]),
    )
    return web.json_response({"ok": True})


async def publish(req):
    u = await auth(req)
    jid = (await req.json()).get("id")
    if not db.q(
        "SELECT 1 FROM jobs WHERE id=? AND uid=? AND status='done'",
        (jid, u["id"]),
    ):
        jerr("notfound", 404)
    if db.q("SELECT 1 FROM feed WHERE work=?", (jid,)):
        db.x("DELETE FROM feed WHERE work=?", (jid,))
        return web.json_response({"published": False})
    db.x("INSERT INTO feed VALUES(?,?,?,?)", (jid, u["id"], db.now(), ""))
    db.add_xp(u["id"], 5)
    return web.json_response({"published": True})


async def feed(req):
    u = await auth(req)
    out = []
    for f in db.q(
        "SELECT f.*,u.name FROM feed f JOIN users u ON u.id=f.uid "
        "ORDER BY f.ts DESC LIMIT 40"
    ):
        j = db.q(
            "SELECT kind,params,files FROM jobs WHERE id=?", (f["work"],)
        )
        if not j:
            continue
        out.append(
            {
                "id": f["work"],
                "uid": f["uid"],
                "name": f["name"],
                "kind": j[0]["kind"],
                "params": json.loads(j[0]["params"]),
                "files": sign_files(json.loads(j[0]["files"]), 0),
                "likes": db.likes_of(f["work"]),
                "liked": bool(
                    db.q(
                        "SELECT 1 FROM likes WHERE uid=? AND work=?",
                        (u["id"], f["work"]),
                    )
                ),
                "ts": f["ts"],
            }
        )
    return web.json_response({"feed": out})


async def like(req):
    u = await auth(req)
    w = (await req.json()).get("id")
    on = db.like_toggle(u["id"], w)
    if on:
        owner = db.q("SELECT uid FROM feed WHERE work=?", (w,))
        if owner:
            db.add_xp(owner[0]["uid"], 2)
    return web.json_response({"liked": on, "likes": db.likes_of(w)})


async def leaderboard(req):
    await auth(req)
    t = db.now() - 7 * 86400
    rows = db.q(
        "SELECT f.uid uid,u.name name,COUNT(l.uid) c FROM feed f "
        "JOIN users u ON u.id=f.uid LEFT JOIN likes l ON l.work=f.work "
        "AND l.ts>? GROUP BY f.uid ORDER BY c DESC LIMIT 10",
        (t,),
    )
    return web.json_response({"top": rows})


async def quests(req):
    u = await auth(req)
    done = {r["key"] for r in db.q("SELECT key FROM quests WHERE uid=?", (u["id"],))}
    return web.json_response(
        {
            "quests": [
                {
                    "key": k,
                    "title": t,
                    "target": n,
                    "progress": min(core.quest_progress(u["id"], m), n),
                    "claimed": k in done,
                }
                for k, t, n, m in core.QDEFS
            ]
        }
    )


async def quest_claim(req):
    u = await auth(req)
    key = (await req.json()).get("key")
    d = next((x for x in core.QDEFS if x[0] == key), None)
    if not d or key in {
        r["key"] for r in db.q("SELECT key FROM quests WHERE uid=?", (u["id"],))
    }:
        jerr("bad")
    if core.quest_progress(u["id"], d[3]) < d[2]:
        jerr("notdone")
    db.x("INSERT INTO quests VALUES(?,?,?)", (u["id"], key, db.now()))
    db.upd(u["id"], balance=db.user(u["id"])["balance"] + 3)
    lvl = db.add_xp(u["id"], 25)
    return web.json_response({"ok": True, "level": lvl})


async def skin(req):
    u = await auth(req)
    s = (await req.json()).get("skin")
    if s not in ("mint", "violet", "magenta", "amber", "ice"):
        jerr("bad")
    need = {"mint": 1, "violet": 3, "magenta": 5, "amber": 7, "ice": 9}[s]
    if int((db.user(u["id"])["xp"] / 100) ** 0.5) + 1 < need:
        jerr("level")
    db.upd(u["id"], skin=s)
    return web.json_response({"ok": True})


async def writeok(req):
    u = await auth(req)
    db.upd(u["id"], write_ok=1)
    return web.json_response({"ok": True})


async def trial(req):
    u = await auth(req)
    d = db.user(u["id"])
    if d["trial_used"]:
        jerr("already")
    db.upd(
        u["id"],
        trial_used=1,
        pro_until=max(d["pro_until"], db.now()) + 3 * 86400,
        tier="pro",
    )
    return web.json_response({"ok": True})


async def key(req):
    u = await auth(req)
    import secrets

    k = "lex_" + secrets.token_urlsafe(24)
    db.upd(u["id"], api_key=k)
    return web.json_response({"key": k})


async def profile(req):
    u = await auth(req)
    if req.method == "GET":
        return web.json_response(json.loads(db.user(u["id"])["doc_profile"] or "{}"))
    db.upd(
        u["id"],
        doc_profile=json.dumps(await req.json(), ensure_ascii=False),
    )
    return web.json_response({"ok": True})


async def email(req):
    u = await auth(req)
    b = await req.json()
    j = db.q(
        "SELECT files FROM jobs WHERE id=? AND uid=? AND status='done'",
        (b.get("id"), u["id"]),
    )
    if not j:
        jerr("notfound", 404)
    if not SMTP_HOST:
        jerr("smtp")
    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = b.get("to", MAIL_FROM)
    msg["Subject"] = "LexKL: документ"
    msg.set_content("Во вложении файлы из LexKL Studio.")
    for f in json.loads(j[0]["files"])[:2]:
        data = gen.load(f["name"])
        msg.add_attachment(
            data,
            maintype="application",
            subtype="octet-stream",
            filename=f["name"],
        )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return web.json_response({"ok": True})


async def bonus(req):
    u = await auth(req)
    d = db.user(u["id"])
    t = db.now()
    if d["last_bonus"] >= t - 86400:
        jerr("already")
    streak = d["streak"] + 1 if d["last_bonus"] >= t - 172800 else 1
    amount = (2 + min(streak, 5)) * (2 if db.is_pro(u["id"], d) else 1)
    db.upd(
        u["id"],
        streak=streak,
        last_bonus=t,
        reminded=0,
        balance=d["balance"] + amount,
    )
    db.ev(u["id"], "bonus")
    return web.json_response(
        {
            "amount": amount,
            "streak": streak,
            "balance": db.user(u["id"])["balance"],
        }
    )


async def promo(req):
    u = await auth(req)
    code = ((await req.json()).get("code") or "").strip().upper()
    r = db.q("SELECT * FROM promos WHERE code=?", (code,))
    if not r:
        jerr("promo")
    p = r[0]
    if p["used"] >= p["max"] or (p["exp"] and p["exp"] < db.now()):
        jerr("promo")
    if p["kind"] == "pro":
        d = db.user(u["id"])
        db.upd(
            u["id"],
            pro_until=max(d["pro_until"], db.now()) + p["value"] * 86400,
            tier="pro",
        )
    else:
        db.upd(
            u["id"],
            balance=db.user(u["id"])["balance"] + p["value"],
        )
    db.x("UPDATE promos SET used=used+1 WHERE code=?", (code,))
    return web.json_response({"ok": True, "kind": p["kind"], "value": p["value"]})


async def invoice(req):
    u = await auth(req)
    body = await req.json()
    plan = body.get("plan")
    pack = body.get("pack")
    gift = int(body.get("gift_to", 0) or 0)
    if plan in PLANS:
        amount, days, sub = PLANS[plan]
        payload = f"{'gift' if gift else 'pro'}:{plan}:{u['id']}:{gift}"
        tg = {
            "title": ("🎁 Подарок PRO " if gift else "PRO ") + plan,
            "description": "Безлимит и приоритет",
            "payload": payload,
            "currency": "XTR",
            "prices": [{"label": "PRO", "amount": amount}],
        }
        if sub:
            tg["subscription_period"] = 2592000
    elif pack in PACKS:
        g, amount = PACKS[pack]
        payload = f"{'giftpack' if gift else 'pack'}:{pack}:{u['id']}:{gift}"
        tg = {
            "title": f"{g} призраков",
            "description": "Пакет 👻",
            "payload": payload,
            "currency": "XTR",
            "prices": [{"label": f"{g} 👻", "amount": amount}],
        }
    else:
        jerr("bad")
    import aiohttp

    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink",
            json=tg,
            proxy=PROXY or None,
        ) as r:
            j = await r.json()
        if not j.get("ok") and tg.get("subscription_period"):
            tg.pop("subscription_period")
            async with s.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink",
                json=tg,
                proxy=PROXY or None,
            ) as r:
                j = await r.json()
    if not j.get("ok"):
        jerr("invoice", 502)
    return web.json_response({"link": j["result"]})


async def improve(req):
    u = await auth(req)
    b = await req.json()
    if not rl("imp" + str(u["id"]), 10, 60):
        jerr("rate", 429)
    text = providers.llm(
        [
            {
                "role": "system",
                "content": "Улучши промпт для генерации: свет, композиция, детали, стиль. До 60 слов, на русском. Верни только промпт.",
            },
            {
                "role": "user",
                "content": f"Стиль {b.get('style')}. {b.get('prompt')}",
            },
        ],
        120,
    )
    src = "llm"
    if not text:
        boost = {
            "Кино": "кинематографичный свет, малая ГРИП, teal-orange",
            "Аниме": "чистые линии, закатный свет",
            "Неон": "неоновые отражения, мокрый асфальт, туман",
            "Акварель": "растечки краски, текстура бумаги",
            "Ретро": "synthwave, зерно плёнки",
            "3D": "изометрия, мягкие тени, AO",
        }
        text = (
            f"{b.get('prompt', '')}, {boost.get(b.get('style'), boost['Кино'])}, "
            "высокая детализация"
        )
        src = "local"
    return web.json_response({"prompt": text, "src": src})


async def lyrics(req):
    u = await auth(req)
    b = await req.json()
    return web.json_response(
        {"lyrics": providers.lyrics(b.get("prompt", ""), b.get("mood", "Мечтательная"))}
    )


async def v1_gen(req):
    key = req.headers.get("Authorization", "").replace("Bearer ", "")
    r = db.q("SELECT id FROM users WHERE api_key=? AND api_key!=''", (key,))
    if not r:
        jerr("auth", 401)
    uid = r[0]["id"]
    if not rl("v1" + str(uid), 12, 60):
        jerr("rate", 429)
    p = await req.json()
    try:
        res = core.submit(uid, p.get("mode"), p)
    except core.Err as e:
        jerr(e.code, 402 if e.code == "nobalance" else 400)
    return web.json_response(res)


async def v1_job(req):
    key = req.headers.get("Authorization", "").replace("Bearer ", "")
    r = db.q("SELECT id FROM users WHERE api_key=? AND api_key!=''", (key,))
    if not r:
        jerr("auth", 401)
    j = db.q(
        "SELECT * FROM jobs WHERE id=? AND uid=?",
        (req.match_info["id"], r[0]["id"]),
    )
    if not j:
        jerr("notfound", 404)
    return web.json_response(
        {
            "status": j[0]["status"],
            "files": [
                WEBAPP_URL + surl(f["name"], r[0]["id"])
                for f in json.loads(j[0]["files"])
            ],
        }
    )


async def admin_stats(req):
    u = await auth(req)
    if u["id"] not in OWNERS:
        jerr("forbidden", 403)
    return web.json_response(
        {
            "users": db.q("SELECT COUNT(*) c FROM users")[0]["c"],
            "pro": db.q(
                "SELECT COUNT(*) c FROM users WHERE pro_until>?", (db.now(),)
            )[0]["c"],
            "jobs": {
                r["status"]: r["c"]
                for r in db.q(
                    "SELECT status,COUNT(*) c FROM jobs GROUP BY status"
                )
            },
            "revenue": db.q("SELECT COALESCE(SUM(amount),0) s FROM payments")[0]["s"],
            "spend": round(db.spend_today(), 2),
            "today": db.q(
                "SELECT COUNT(*) c FROM jobs WHERE ts>?", (db.now() - 86400,)
            )[0]["c"],
            "events": db.q(
                "SELECT name,COUNT(*) c FROM events WHERE ts>? GROUP BY name "
                "ORDER BY c DESC LIMIT 8",
                (db.now() - 7 * 86400,),
            ),
            "flags": db.q("SELECT * FROM flags"),
            "providers": db.q(
                "SELECT provider,ROUND(SUM(cost),2) s FROM spend WHERE ts>? "
                "GROUP BY provider",
                (db.now() - 86400,),
            ),
        }
    )


async def admin_user(req):
    u = await auth(req)
    if u["id"] not in OWNERS:
        jerr("forbidden", 403)
    b = await req.json()
    uid = int(b.get("uid", 0))
    days = int(b.get("days", 0))
    bal = int(b.get("balance", 0))
    if not uid:
        jerr("bad")
    d = db.user(uid)
    if days:
        db.upd(
            uid,
            pro_until=max(d["pro_until"], db.now()) + days * 86400,
            tier=b.get("tier", "pro"),
        )
    if bal:
        db.upd(uid, balance=max(0, d["balance"] + bal))
    return web.json_response({"user": db.user(uid)})


async def admin_promo(req):
    u = await auth(req)
    if u["id"] not in OWNERS:
        jerr("forbidden", 403)
    b = await req.json()
    code = (b.get("code") or "").strip().upper()
    if not code:
        jerr("bad")
    db.x(
        "INSERT OR REPLACE INTO promos(code,kind,value,max,used,exp) "
        "VALUES(?,?,?,?,0,?)",
        (
            code,
            b.get("kind", "balance"),
            int(b.get("value", 5)),
            int(b.get("max", 100)),
            db.now() + int(b.get("days", 30)) * 86400,
        ),
    )
    return web.json_response({"ok": True, "code": code})


async def admin_broadcast(req):
    u = await auth(req)
    if u["id"] not in OWNERS:
        jerr("forbidden", 403)
    text = (await req.json()).get("text", "")
    if not text.strip():
        jerr("bad")
    db.x(
        "INSERT INTO jobs(id,uid,kind,params,cost,ts) VALUES(?,?,?,?,0,?)",
        (
            "b" + str(db.now()),
            u["id"],
            "broadcast",
            json.dumps({"text": text}, ensure_ascii=False),
            db.now(),
        ),
    )
    return web.json_response({"ok": True})


async def admin_flag(req):
    u = await auth(req)
    if u["id"] not in OWNERS:
        jerr("forbidden", 403)
    b = await req.json()
    db.x(
        "INSERT OR REPLACE INTO flags VALUES(?,?,?)",
        (b.get("key", ""), b.get("val", "1"), int(b.get("pct", 100))),
    )
    return web.json_response({"ok": True})


async def files(req):
    name = req.match_info["name"]
    if "/" in name or ".." in name:
        raise web.HTTPNotFound()
    u = int(req.query.get("u", 0))
    e = int(req.query.get("e", 0))
    s = req.query.get("s", "")
    if e < db.now() or (
        hmac.new(
            BOT_TOKEN.encode(), f"{name}:{u}:{e}".encode(), hashlib.sha256
        ).hexdigest()[:32]
        != s
    ):
        raise web.HTTPForbidden()
    try:
        data = gen.load(name)
    except Exception:
        raise web.HTTPNotFound()
    mime = (
        "image/png"
        if name.endswith(".png")
        else "image/svg+xml"
        if name.endswith(".svg")
        else "image/webp"
        if name.endswith(".webp")
        else "video/mp4"
        if name.endswith(".mp4")
        else "image/gif"
        if name.endswith(".gif")
        else "audio/mpeg"
        if name.endswith(".mp3")
        else "audio/wav"
        if name.endswith(".wav")
        else "application/octet-stream"
    )
    return web.Response(
        body=data,
        content_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


async def on_start(app):
    if os.getenv("WORKER_EMBEDDED", "1") != "0":
        app["workers"] = [
            asyncio.create_task(worker.loop(i)) for i in range(2)
        ]
        app["crons"] = asyncio.create_task(worker.crons())


app = web.Application(
    client_max_size=6 * 1024 * 1024,
    middlewares=[sec],
)
app.add_routes(
    [
        web.get("/", index),
        web.get("/healthz", health),
        web.get("/metrics", metrics),
        web.get("/api/state", state),
        web.post("/api/jobs", jobs),
        web.get("/api/jobs/{id}", job_get),
        web.post("/api/fav", fav),
        web.post("/api/delete", delete),
        web.post("/api/restore", restore),
        web.post("/api/send", send),
        web.post("/api/publish", publish),
        web.get("/api/feed", feed),
        web.post("/api/like", like),
        web.get("/api/leaderboard", leaderboard),
        web.get("/api/quests", quests),
        web.post("/api/quest/claim", quest_claim),
        web.post("/api/skin", skin),
        web.post("/api/writeok", writeok),
        web.post("/api/trial", trial),
        web.post("/api/key", key),
        web.get("/api/profile", profile),
        web.post("/api/profile", profile),
        web.post("/api/email", email),
        web.post("/api/bonus", bonus),
        web.post("/api/promo", promo),
        web.post("/api/invoice", invoice),
        web.post("/api/improve", improve),
        web.post("/api/lyrics", lyrics),
        web.post("/v1/generate", v1_gen),
        web.get("/v1/jobs/{id}", v1_job),
        web.get("/api/admin/stats", admin_stats),
        web.post("/api/admin/user", admin_user),
        web.post("/api/admin/promo", admin_promo),
        web.post("/api/admin/broadcast", admin_broadcast),
        web.post("/api/admin/flag", admin_flag),
        web.get("/files/{name}", files),
    ]
)
app.on_startup.append(on_start)

if __name__ == "__main__":
    web.run_app(app, port=int(os.environ.get("PORT", "8080")))
