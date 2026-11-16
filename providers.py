import hashlib
import json
import math
import os
import re
import time
import urllib.request

import db
import gen
from config import *

def _post(url, payload, key, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def llm(messages, max_tokens=300):
    if not LLM_KEY:
        return None
    try:
        return _post(
            LLM_BASE + "/chat/completions",
            {"model": LLM_MODEL, "messages": messages, "max_tokens": max_tokens},
            LLM_KEY,
        )["choices"][0]["message"]["content"].strip()
    except Exception:
        return None

def budget_ok(cost):
    return db.spend_today() + cost <= DAILY_BUDGET

def embed(text):
    if LLM_KEY:
        try:
            return _post(
                LLM_BASE + "/embeddings",
                {"model": EMBED_MODEL, "input": text[:2000]},
                LLM_KEY,
            )["data"][0]["embedding"]
        except Exception:
            pass
    v = [0.0] * 256
    for w in re.findall(r"[а-яa-z0-9]+", text.lower()):
        for i in range(len(w) - 2):
            v[int(hashlib.sha256(w[i:i+3].encode()).hexdigest()[:4], 16) % 256] += 1
    n = math.sqrt(sum(a * a for a in v)) or 1
    return [a / n for a in v]

def cos(a, b):
    if len(a) != len(b):
        return 0
    return sum(x * y for x, y in zip(a, b))

def sem_cache(kind, params):
    e = embed(params.get("prompt", ""))
    for r in db.q(
        "SELECT emb,files FROM cache WHERE kind=? ORDER BY ts DESC LIMIT 40", (kind,)
    ):
        try:
            if cos(e, json.loads(r["emb"])) > 0.93:
                return json.loads(r["files"])
        except Exception:
            pass
    return None

def photo(prompt, style, aspect, seed, ref, strength, hq, watermark):
    if PHOTO_API_URL and PHOTO_API_KEY and budget_ok(0.04):
        try:
            size = {
                "1:1": "1024x1024",
                "4:5": "1024x1280",
                "16:9": "1280x720",
                "9:16": "720x1280",
            }.get(aspect, "1024x1280")
            r = _post(
                PHOTO_API_URL.rstrip("/") + "/images/generations",
                {
                    "model": PHOTO_MODEL,
                    "prompt": f"{prompt}, стиль {style}",
                    "size": size,
                    "response_format": "b64_json",
                    "n": 1,
                },
                PHOTO_API_KEY,
                120,
            )
            import base64

            data = base64.b64decode(r["data"][0]["b64_json"])
            db.spend_add("photo_api", 0.04)
            return gen.post_png(data, watermark)
        except Exception:
            pass
    svg = gen.photo_svg(seed, style, aspect, prompt, 0, ref, strength)
    return gen.post_png(gen.svg2png(svg), watermark)

def video(prompt, style, aspect, seed, dur, photo_png, song_path):
    if VIDEO_API_URL and VIDEO_API_KEY and budget_ok(0.3):
        try:
            r = _post(
                VIDEO_API_URL.rstrip("/") + "/generations",
                {"prompt": prompt, "duration": dur},
                VIDEO_API_KEY,
                60,
            )
            gid = r.get("id")
            url = None
            for _ in range(60):
                time.sleep(4)
                with urllib.request.urlopen(
                    urllib.request.Request(
                        VIDEO_API_URL.rstrip("/") + "/generations/" + str(gid),
                        headers={"Authorization": "Bearer " + VIDEO_API_KEY},
                    ),
                    timeout=30,
                ) as rr:
                    s = json.load(rr)
                if s.get("status") in ("completed", "succeeded"):
                    url = s.get("video_url") or s.get("url")
                    break
                if s.get("status") in ("failed", "error"):
                    break
            if url:
                with urllib.request.urlopen(url, timeout=120) as rr:
                    data = rr.read()
                db.spend_add("video_api", 0.3)
                return data
        except Exception:
            pass
    return gen.kenburns(photo_png, song_path, dur)

def song(prompt, lyrics, mood, tempo, dur, duet, seed):
    if SUNO_API_URL and SUNO_API_KEY and budget_ok(0.1):
        try:
            r = _post(
                SUNO_API_URL.rstrip("/") + "/generate",
                {"prompt": prompt, "lyrics": lyrics or "", "duration": dur},
                SUNO_API_KEY,
                60,
            )
            gid = r.get("id")
            url = None
            for _ in range(60):
                time.sleep(4)
                with urllib.request.urlopen(
                    urllib.request.Request(
                        SUNO_API_URL.rstrip("/") + "/generate/" + str(gid),
                        headers={"Authorization": "Bearer " + SUNO_API_KEY},
                    ),
                    timeout=30,
                ) as rr:
                    s = json.load(rr)
                if s.get("status") in ("completed", "succeeded"):
                    url = s.get("audio_url") or s.get("url")
                    break
                if s.get("status") in ("failed", "error"):
                    break
            if url:
                with urllib.request.urlopen(url, timeout=120) as rr:
                    data = rr.read()
                db.spend_add("suno", 0.1)
                return data
        except Exception:
            pass
    wav = gen.song_wav_bytes(seed, tempo, mood, dur, duet)
    mp3 = gen.to_mp3(wav)
    return mp3 or wav

def stt(data):
    if LLM_KEY:
        try:
            b = "----lex" + str(time.time())
            body = (
                f"--{b}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
                f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"v.ogg\"\r\n"
                "Content-Type: audio/ogg\r\n\r\n"
            ).encode() + data + f"\r\n--{b}--\r\n".encode()
            req = urllib.request.Request(
                LLM_BASE + "/audio/transcriptions",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={b}",
                    "Authorization": "Bearer " + LLM_KEY,
                },
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["text"]
        except Exception:
            pass
    try:
        from faster_whisper import WhisperModel

        m = WhisperModel("tiny", device="cpu")
        seg, _ = m.transcribe(gen.tmp(data, ".ogg"))
        return " ".join(s.text for s in seg)
    except Exception:
        return None

def tts(text, out):
    try:
        import asyncio
        import edge_tts

        asyncio.run(edge_tts.Communicate(text, "ru-RU-DmitryNeural").save(out))
        return True
    except Exception:
        pass
    if LLM_KEY:
        try:
            req = urllib.request.Request(
                LLM_BASE + "/audio/speech",
                data=json.dumps(
                    {"model": "tts-1", "voice": "onyx", "input": text[:400]}
                ).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + LLM_KEY,
                },
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                open(out, "wb").write(r.read())
            return True
        except Exception:
            pass
    return False

def vision(data):
    import base64

    return llm(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Опиши изображение как промпт для генерации, до 40 слов, на русском.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(data).decode()
                        },
                    },
                ],
            }
        ],
        120,
    )

def lyrics(prompt, mood):
    t = llm(
        [
            {
                "role": "system",
                "content": "Напиши 4 строки песни на русском, рифма AABB, тема от пользователя. Верни только строки.",
            },
            {"role": "user", "content": f"{prompt} настроение {mood}"},
        ],
        120,
    )
    return t or gen.lyrics_local(prompt, mood)
