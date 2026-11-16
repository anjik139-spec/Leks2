import asyncio
import csv
import hashlib
import io as _io
import json
import os

import aiohttp

import db
import gen
import providers
from config import BOT_API_URL, BOT_TOKEN, CHANNEL_ID, DATA, OWNERS, WORKERS

PROXY = os.getenv("PROXY_URL", "")


async def tg(method, payload=None, files=None):
    async with aiohttp.ClientSession() as session:
        if files:
            form = aiohttp.FormData()
            for key, value in (payload or {}).items():
                form.add_field(key, str(value))
            for key, (name, data, mime) in files.items():
                form.add_field(key, data, filename=name, content_type=mime)
            async with session.post(
                f"{BOT_API_URL}/bot{BOT_TOKEN}/{method}",
                data=form,
                proxy=PROXY or None,
            ) as response:
                return await response.json()
        async with session.post(
            f"{BOT_API_URL}/bot{BOT_TOKEN}/{method}",
            json=payload or {},
            proxy=PROXY or None,
        ) as response:
            return await response.json()


async def send_message(uid, text):
    result = await tg("sendMessage", {"chat_id": uid, "text": text})
    if not result.get("ok") and result.get("parameters", {}).get("retry_after"):
        await asyncio.sleep(result["parameters"]["retry_after"])
        await tg("sendMessage", {"chat_id": uid, "text": text})


async def alert(text):
    for owner in OWNERS:
        await send_message(owner, "🚨 " + text)


def render(job, params):
    kind = job["kind"]
    uid = job["uid"]
    seed = int(
        hashlib.sha256((params.get("prompt", "") + job["id"]).encode()).hexdigest()[:8],
        16,
    )
    base = os.path.join(DATA, "files", job["id"])
    files = []

    def put(name, data, mime):
        gen.save(name, data)
        files.append({"name": name, "mime": mime})

    if kind == "photo":
        png = providers.photo(
            params.get("prompt", ""),
            params.get("style", "Кино"),
            params.get("aspect", "4:5"),
            seed,
            params.get("ref"),
            float(params.get("strength", 0.45)),
            params.get("hq"),
            params.get("watermark"),
        )
        put(job["id"] + ".png", png, "image/png")
        svg = gen.photo_svg(
            seed,
            params.get("style", "Кино"),
            params.get("aspect", "4:5"),
            params.get("prompt", ""),
            0,
            params.get("ref"),
            float(params.get("strength", 0.45)),
        )
        put(job["id"] + ".svg", svg.encode(), "image/svg+xml")
    elif kind == "video":
        png = providers.photo(
            params.get("prompt", ""),
            params.get("style", "Кино"),
            params.get("aspect", "16:9"),
            seed,
            None,
            0.45,
            params.get("hq"),
            params.get("watermark"),
        )
        wav = gen.song_wav_bytes(
            seed, 100, "Мечтательная", int(params.get("dur", 6))
        )
        mp4 = providers.video(
            params.get("prompt", ""),
            params.get("style", "Кино"),
            params.get("aspect", "16:9"),
            seed,
            int(params.get("dur", 6)),
            png,
            wav,
        )
        if mp4:
            put(job["id"] + ".mp4", mp4, "video/mp4")
        else:
            put(
                job["id"] + ".svg",
                gen.photo_svg(
                    seed,
                    params.get("style", "Кино"),
                    params.get("aspect", "16:9"),
                    params.get("prompt", ""),
                    int(params.get("dur", 6)),
                ).encode(),
                "image/svg+xml",
            )
    elif kind == "song":
        data = providers.song(
            params.get("prompt", ""),
            params.get("lyrics", ""),
            params.get("mood", "Мечтательная"),
            int(params.get("tempo", 104)),
            int(params.get("dur", 10)),
            params.get("duet"),
            seed,
        )
        is_mp3 = data[:3] == b"ID3" or data[:2] == b"\xff\xfb"
        put(
            job["id"] + (".mp3" if is_mp3 else ".wav"),
            data,
            "audio/mpeg" if is_mp3 else "audio/wav",
        )
    elif kind == "clip":
        source = db.q(
            "SELECT files,params FROM jobs WHERE id=? AND status='done'",
            (params.get("photo"),),
        )
        song = (
            db.q(
                "SELECT files FROM jobs WHERE id=? AND status='done'",
                (params.get("song"),),
            )
            if params.get("song")
            else []
        )
        if source:
            png = gen.load(json.loads(source[0]["files"])[0]["name"])
        else:
            png = providers.photo(
                params.get("prompt", "clip"),
                "Кино",
                "4:5",
                seed,
                None,
                0.45,
                False,
                False,
            )
        wav = None
        if song:
            first = json.loads(song[0]["files"])[0]
            wav = gen.load(first["name"])
            if first["name"].endswith(".mp3"):
                src = gen.tmp(wav, ".mp3")
                out = src[:-4] + ".wav"
                if gen.ff("-i", src, out):
                    with open(out, "rb") as file:
                        wav = file.read()
        if not wav:
            wav = gen.song_wav_bytes(
                seed, 104, "Мечтательная", int(params.get("dur", 8))
            )
        mp4 = gen.kenburns(png, wav, int(params.get("dur", 8)))
        if mp4:
            put(job["id"] + ".mp4", mp4, "video/mp4")
        else:
            put(job["id"] + ".png", png, "image/png")
    elif kind == "gif":
        source = db.q(
            "SELECT files FROM jobs WHERE id=? AND status='done'",
            (params.get("work"),),
        )
        mp4 = gen.load(json.loads(source[0]["files"])[0]["name"]) if source else b""
        gif = gen.to_gif(mp4)
        if not gif:
            raise ValueError("нужен ffmpeg и mp4-источник")
        put(job["id"] + ".gif", gif, "image/gif")
    elif kind == "ringtone":
        source = db.q(
            "SELECT files FROM jobs WHERE id=? AND status='done'",
            (params.get("work"),),
        )
        wav = gen.load(json.loads(source[0]["files"])[0]["name"]) if source else b""
        result = gen.ringtone(wav)
        if not result:
            raise ValueError("нужен ffmpeg")
        put(job["id"] + ".mp3", result, "audio/mpeg")
    elif kind == "cover":
        source = db.q("SELECT params FROM jobs WHERE id=?", (params.get("work"),))
        prompt = json.loads(source[0]["params"])["prompt"] if source else "cover"
        png = providers.photo(
            prompt + ", обложка трека",
            params.get("style", "Неон"),
            "1:1",
            seed,
            None,
            0.45,
            False,
            params.get("watermark"),
        )
        put(job["id"] + ".png", png, "image/png")
    elif kind == "edit":
        source = db.q(
            "SELECT files,params FROM jobs WHERE id=? AND status='done'",
            (params.get("work"),),
        )
        png = gen.load(json.loads(source[0]["files"])[0]["name"]) if source else b""
        operation = params.get("op")
        if operation == "upscale":
            out = gen.upscale(png)
        elif operation == "bgremove":
            out = gen.sticker_webp(png)
        elif operation == "inpaint":
            out = gen.inpaint(
                png,
                params.get("mask"),
                params.get("prompt", ""),
                params.get("style", "Кино"),
                seed,
            )
        else:
            out = providers.photo(
                params.get("prompt", ""),
                params.get("style", "Кино"),
                "4:5",
                seed,
                params.get("ref"),
                float(params.get("strength", 0.45)),
                False,
                params.get("watermark"),
            )
        put(job["id"] + ".png", out, "image/png")
    elif kind in ("sticker", "emoji"):
        source = db.q(
            "SELECT files FROM jobs WHERE id=? AND status='done'",
            (params.get("work"),),
        )
        png = gen.load(json.loads(source[0]["files"])[0]["name"]) if source else b""
        put(
            job["id"] + ".webp",
            gen.sticker_webp(png, kind == "emoji"),
            "image/webp",
        )
    elif kind == "doc":
        files = gen.make_doc(
            params.get("tpl", "zayavlenie"),
            params.get("fields", {}),
            base,
        )
    elif kind == "batch":
        rows = list(csv.DictReader(_io.StringIO(params.get("csv", ""))))[:50]
        for index, row in enumerate(rows):
            jid = f"j{db.now() * 1000}{index}b"
            db.x(
                "INSERT INTO jobs(id,uid,kind,params,cost,ts,priority) VALUES(?,?,?,?,0,?,?)",
                (
                    jid,
                    uid,
                    "doc",
                    json.dumps(
                        {"tpl": params.get("tpl"), "fields": row},
                        ensure_ascii=False,
                    ),
                    db.now(),
                    1,
                ),
            )
        put(
            job["id"] + ".txt",
            f"Создано документов: {len(rows)}".encode(),
            "text/plain",
        )
    elif kind == "broadcast":
        return files
    db.cache_set(
        hashlib.sha256(
            json.dumps(
                {k: v for k, v in params.items() if k not in ("ref", "mask")},
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        files,
        providers.embed(params.get("prompt", "")),
        kind,
    )
    return files


async def one(job):
    params = json.loads(job["params"])
    if job["kind"] == "broadcast":
        for user in db.q("SELECT id FROM users"):
            await send_message(user["id"], params.get("text", ""))
            await asyncio.sleep(0.05)
        db.x("UPDATE jobs SET status='done',files='[]' WHERE id=?", (job["id"],))
        return
    files = await asyncio.to_thread(render, job, params)
    db.x(
        "UPDATE jobs SET status='done',files=? WHERE id=?",
        (json.dumps(files), job["id"]),
    )
    db.add_xp(job["uid"], 10)
    user = db.user(job["uid"])
    if not user["ref_paid"] and user["ref_by"]:
        referrer = db.user(user["ref_by"])
        db.upd(
            user["ref_by"],
            balance=referrer["balance"] + 5,
            ref_count=referrer["ref_count"] + 1,
        )
        db.upd(job["uid"], balance=user["balance"] + 5, ref_paid=1)
        await send_message(user["ref_by"], "👻 +5 призраков за друга!")


async def loop(worker_id):
    while True:
        job = db.claim()
        if not job:
            await asyncio.sleep(0.6 + worker_id * 0.1)
            continue
        try:
            await one(job)
        except Exception as exc:
            db.retry(job, str(exc))
            if job["tries"] >= 3:
                await alert(
                    f"job {job['id']} ({job['kind']}) failed: {str(exc)[:150]}"
                )


async def crons():
    last = {"rem": 0, "dig": 0, "win": 0, "chan": 0}
    while True:
        now = db.now()
        if now - last["rem"] > 3600:
            last["rem"] = now
            for user in db.q(
                "SELECT * FROM users WHERE streak>0 AND reminded=0 AND last_bonus<? AND last_bonus>?",
                (now - 72000, now - 86400),
            ):
                await send_message(
                    user["id"],
                    "🔥 Серия сгорит через несколько часов! Забери бонус в меню.",
                )
                db.upd(user["id"], reminded=1)
        if now - last["win"] > 86400:
            last["win"] = now
            for user in db.q(
                "SELECT * FROM users WHERE winback=0 AND last_seen>0 AND last_seen<?",
                (now - 7 * 86400,),
            ):
                code = f"WB{user['id']}"
                db.x(
                    "INSERT OR REPLACE INTO promos(code,kind,value,max,used,exp) VALUES(?,?,?,?,0,?)",
                    (code, "balance", 10, 1, now + 7 * 86400),
                )
                await send_message(
                    user["id"],
                    f"👻 Скучали! Промокод {code} на 10 призраков — введи в меню → PRO.",
                )
                db.upd(user["id"], winback=1)
        if now - last["dig"] > 7 * 86400:
            last["dig"] = now
            for user in db.q("SELECT id FROM users"):
                stats = {
                    kind: db.q(
                        "SELECT COUNT(*) c FROM jobs WHERE uid=? AND kind=? AND status='done' AND ts>?",
                        (user["id"], kind, now - 7 * 86400),
                    )[0]["c"]
                    for kind in ("photo", "video", "song", "doc")
                }
                stats["gen"] = sum(stats.values())
                if stats["gen"]:
                    card = gen.digest_card(user["id"], stats)
                    gen.save(f"dig_{user['id']}.png", card)
                    await tg(
                        "sendPhoto",
                        {"chat_id": user["id"], "caption": "📊 Твои итоги недели"},
                        {"photo": (f"dig_{user['id']}.png", card, "image/png")},
                    )
        if CHANNEL_ID and now - last["chan"] > 86400:
            last["chan"] = now
            top = db.q(
                "SELECT f.work,f.uid,COUNT(l.uid) c FROM feed f LEFT JOIN likes l "
                "ON l.work=f.work GROUP BY f.work ORDER BY c DESC LIMIT 1"
            )
            if top:
                job = db.q(
                    "SELECT files,params FROM jobs WHERE id=?", (top[0]["work"],)
                )
                if job:
                    files = json.loads(job[0]["files"])
                    params = json.loads(job[0]["params"])
                    png = gen.load(files[0]["name"])
                    await tg(
                        "sendPhoto",
                        {
                            "chat_id": CHANNEL_ID,
                            "caption": "🏆 Работа дня: "
                            + params.get("prompt", "")[:120]
                            + "\n👉 @LexKLBot",
                        },
                        {"photo": ("d.png", png, "image/png")},
                    )
        await asyncio.sleep(30)


async def main():
    await asyncio.gather(*[loop(i) for i in range(WORKERS)], crons())


if __name__ == "__main__":
    asyncio.run(main())
