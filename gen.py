import array
import hashlib
import json
import math
import os
import random
import re
import struct
import wave

from config import DATA, LLM_BASE, LLM_KEY, LLM_MODEL, MODERATION

MOD_RE = re.compile(
    "|".join(w.strip() for w in MODERATION.split(",") if w.strip()),
    re.I,
)
STYLES = {
    "Кино": (("#0e3a4a", "#177e89", "#ff8c42", "#ffd23f"), False),
    "Аниме": (("#7d9bff", "#ffb7d5", "#fff3b0", "#ff5ca8"), False),
    "Неон": (("#0b0b2a", "#7c5cff", "#ff5ca8", "#38bdf8"), True),
    "Акварель": (("#b9dcff", "#d7f0e3", "#ffd9e8", "#fff3b0"), False),
    "Ретро": (("#2b1055", "#7597de", "#ff6ec7", "#ffb02e"), True),
    "3D": (("#123c69", "#3aaed8", "#f6f7fb", "#ffb02e"), False),
}
DIMS = {"1:1": (800, 800), "4:5": (800, 1000), "16:9": (1000, 562), "9:16": (562, 1000)}


def esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def photo_svg(seed, style, aspect, title, anim=0, ref=None):
    r = random.Random(seed)
    pal, night = STYLES.get(style, STYLES["Кино"])
    W, H = DIMS.get(aspect, (800, 1000))
    sx = int(W * (0.25 + r.random() * 0.5))
    sy = int(H * (0.2 + r.random() * 0.2))
    sr = int(W * (0.09 + r.random() * 0.08))
    s = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{pal[0]}"/>'
        f'<stop offset=".55" stop-color="{pal[1]}"/>'
        f'<stop offset="1" stop-color="{pal[2]}"/></linearGradient></defs>'
        f'<rect width="{W}" height="{H}" fill="url(#g)"/>'
    )
    if ref:
        s += (
            f'<image href="{ref}" width="{W}" height="{H}" '
            f'preserveAspectRatio="xMidYMid slice" opacity=".45"/>'
        )
    if night:
        s += "".join(
            f'<circle cx="{int(r.random() * W)}" cy="{int(r.random() * H * 0.6)}" '
            f'r="{r.random() * 2 + 0.6:.1f}" fill="#fff" '
            f'opacity="{0.3 + r.random() * 0.7:.2f}"/>'
            for _ in range(40)
        )
    sun = "#f4f1de" if night else pal[3]
    s += f'<circle cx="{sx}" cy="{sy}" r="{sr}" fill="{sun}">'
    if anim:
        s += (
            f'<animate attributeName="r" values="{sr};{int(sr * 1.12)};{sr}" '
            f'dur="{anim}s" repeatCount="indefinite"/>'
        )
    s += "</circle>"
    for layer in range(3):
        base = H * (0.55 + layer * 0.14)
        d = f"M0 {H} L0 {int(base)}"
        for i in range(9):
            d += f" L{int(W * i / 8)} {int(base - r.random() * H * 0.16)}"
        s += (
            f'<path d="{d} L{W} {H} Z" fill="{pal[layer % 2]}" '
            f'opacity="{0.5 + layer * 0.25:.2f}"/>'
        )
    gx = int(W * (0.15 + r.random() * 0.6))
    gy = int(H * (0.45 + r.random() * 0.2))
    s += (
        f'<g transform="translate({gx} {gy}) scale({W * 0.14 / 24:.2f})">'
    )
    if anim:
        s += (
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="{gx} {gy};{gx} {gy - 26};{gx} {gy}" '
            f'dur="{anim}s" repeatCount="indefinite"/>'
        )
    s += (
        '<path d="M12 2.6c-4.6 0-7.6 3.5-7.6 8v9.8l2.5-2 2.5 2 2.6-2 '
        '2.6 2 2.5-2 2.5 2V10.6c0-4.5-3-8-7.6-8Z" fill="#5fe3c9"/>'
        '<ellipse cx="9.3" cy="10.4" rx="1.5" ry="2.1" fill="#fff"/>'
        '<ellipse cx="14.7" cy="10.4" rx="1.5" ry="2.1" fill="#fff"/></g>'
    )
    if anim:
        s += (
            f'<ellipse cx="0" cy="{int(H * 0.16)}" rx="{int(W * 0.3)}" '
            f'ry="{int(W * 0.08)}" fill="#fff" opacity=".8">'
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="{-int(W * 0.3)} 0;{W + int(W * 0.3)} 0" '
            f'dur="{anim * 3}s" repeatCount="indefinite"/></ellipse>'
        )
    s += (
        f'<rect y="{H - 64}" width="{W}" height="64" fill="rgba(8,10,26,.55)"/>'
        f'<text x="24" y="{H - 26}" font-family="sans-serif" font-size="24" '
        f'font-weight="700" fill="#fff">{esc(title[:42])}</text></svg>'
    )
    return s


def wave_svg(seed):
    r = random.Random(seed)
    bars = "".join(
        f'<rect x="{i * 10 + 6}" y="{(100 - (14 + r.random() * 70)) / 2:.0f}" '
        f'width="6" height="{14 + r.random() * 70:.0f}" rx="3" fill="url(#w)"/>'
        for i in range(40)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 412 200">'
        '<defs><linearGradient id="w" x1="0" y1="0" x2="1" y2="1">'
        '<stop stop-color="#2fd48f"/><stop offset="1" stop-color="#38bdf8"/>'
        f'</linearGradient></defs><rect width="412" height="200" fill="#10143a"/>{bars}</svg>'
    )


def song_wav(seed, tempo, mood, dur, path):
    r = random.Random(seed)
    sr = 22050
    n = int(sr * dur)
    buf = array.array("h", [0]) * n
    minor = mood == "Тревожная"
    scale = [0, 3, 5, 7, 10] if minor else [0, 2, 4, 7, 9]
    root = 196 if minor else 220
    step = 60 / tempo / 2

    def add(start, freq, length, amp, kind="sine"):
        i0 = int(start * sr)
        ln = int(length * sr)
        for i in range(ln):
            if i0 + i >= n:
                break
            env = min(1, i / (sr * 0.01)) * math.exp(-3 * i / ln)
            t_i = i / sr
            if kind == "sine":
                v = math.sin(2 * math.pi * freq * t_i)
            else:
                v = 2 * ((freq * t_i) % 1) - 1
            buf[i0 + i] = max(
                -32000,
                min(32000, buf[i0 + i] + int(v * env * amp * 32767)),
            )

    i = 0
    t = 0.0
    while t < dur:
        if i % 2 == 0 or r.random() > 0.5:
            deg = r.choice(scale) + (12 if r.random() > 0.7 else 0)
            add(
                t,
                root * 2 ** (deg / 12),
                step * 1.8,
                0.22,
                "tri" if mood == "Lo-fi" else "sine",
            )
        if i % 4 == 0:
            add(
                t,
                root / 2 * 2 ** (scale[(i // 4) % len(scale)] / 12),
                step * 3.5,
                0.25,
            )
        if i % 2 == 1:
            i0 = int(t * sr)
            for k in range(int(sr * 0.04)):
                if i0 + k < n:
                    buf[i0 + k] = max(
                        -32000,
                        min(
                            32000,
                            buf[i0 + k]
                            + int(
                                (r.random() * 2 - 1)
                                * math.exp(-40 * k / sr)
                                * 0.08
                                * 32767
                            ),
                        ),
                    )
        t += step
        i += 1
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(buf.tobytes())


DOCTPL = {
    "zayavlenie": {
        "title": "Заявление",
        "fields": [
            ("org", "Кому (организация/ФИО)"),
            ("fio", "От кого (ФИО)"),
            ("text", "Текст заявления"),
            ("date", "Дата"),
        ],
        "body": lambda d: (
            f"Кому: {d.get('org', '')}\nОт: {d.get('fio', '')}\n\n"
            f"ЗАЯВЛЕНИЕ\n\n{d.get('text', '')}\n\n"
            f"Дата: {d.get('date', '')}    Подпись: ____________"
        ),
    },
    "otchet": {
        "title": "Отчёт",
        "fields": [
            ("org", "Организация"),
            ("fio", "Исполнитель"),
            ("period", "Период"),
            ("text", "Содержание отчёта"),
        ],
        "body": lambda d: (
            f"{d.get('org', '')}\nИсполнитель: {d.get('fio', '')}\n"
            f"Период: {d.get('period', '')}\n\nОТЧЁТ\n\n{d.get('text', '')}\n\n"
            "Подпись: ____________"
        ),
    },
    "dogovor": {
        "title": "Договор",
        "fields": [
            ("a", "Сторона 1"),
            ("b", "Сторона 2"),
            ("subject", "Предмет договора"),
            ("sum", "Сумма"),
            ("date", "Дата"),
        ],
        "body": lambda d: (
            f"ДОГОВОР\n\nСторона 1: {d.get('a', '')}\n"
            f"Сторона 2: {d.get('b', '')}\n"
            f"Предмет: {d.get('subject', '')}\n"
            f"Сумма: {d.get('sum', '')}\n\n"
            "1. Стороны обязуются исполнять условия договора.\n"
            "2. Оплата производится в порядке, согласованном сторонами.\n"
            "3. Споры решаются путём переговоров.\n\n"
            f"Дата: {d.get('date', '')}\n"
            "Подписи: ____________  ____________"
        ),
    },
}


def make_doc(tpl, fields, base):
    template = DOCTPL.get(tpl, DOCTPL["zayavlenie"])
    text = template["body"](fields)
    files = []
    from docx import Document

    doc = Document()
    doc.add_heading(template["title"], 0)
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(base + ".docx")
    files.append(
        {
            "name": os.path.basename(base) + ".docx",
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rc

    font = "DejaVu"
    font_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    )
    for path in font_paths:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(font, path))
            break
    else:
        font = "Helvetica"
    c = rc.Canvas(base + ".pdf", pagesize=A4)
    width, height = A4
    y = height - 60
    c.setFont(font, 11)
    for line in text.split("\n"):
        for chunk in [line[i : i + 90] for i in range(0, len(line), 90)] or [""]:
            if y < 50:
                c.showPage()
                c.setFont(font, 11)
                y = height - 60
            c.drawString(50, y, chunk)
            y -= 16
    c.save()
    files.append(
        {
            "name": os.path.basename(base) + ".pdf",
            "mime": "application/pdf",
        }
    )
    return files


def improve_sync(prompt, style):
    if LLM_KEY:
        import urllib.request

        req = urllib.request.Request(
            LLM_BASE + "/chat/completions",
            data=json.dumps(
                {
                    "model": LLM_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Улучши промпт для генерации изображения: добавь свет, "
                                "композицию, детали, стиль. Верни только промпт, "
                                "до 60 слов, на русском."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Стиль: {style}. Промпт: {prompt}",
                        },
                    ],
                    "max_tokens": 120,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + LLM_KEY,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return (
                    json.load(response)["choices"][0]["message"]["content"].strip(),
                    "llm",
                )
        except Exception:
            pass
    boost = {
        "Кино": "кинематографичный свет, малая глубина резкости, цветокоррекция teal-orange",
        "Аниме": "чистые линии, выразительные глаза, мягкий закатный свет",
        "Неон": "неоновые отражения, мокрый асфальт, объёмный туман, киберпанк",
        "Акварель": "мягкие растечки краски, текстура бумаги, пастельные тона",
        "Ретро": "synthwave-палитра, хромовые блики, зерно плёнки, закатное солнце",
        "3D": "изометрия, мягкие тени, ambient occlusion, чистый рендер",
    }
    return (
        f"{prompt}, {boost.get(style, boost['Кино'])}, высокая детализация, "
        "композиция по правилу третей",
        "local",
    )


def render(j, params):
    kind = j["kind"]
    seed = int(
        hashlib.sha256((params.get("prompt", "") + j["id"]).encode()).hexdigest()[:8],
        16,
    )
    base = os.path.join(DATA, "files", j["id"])
    if kind in ("photo", "video"):
        svg = photo_svg(
            seed,
            params.get("style", "Кино"),
            params.get("aspect", "4:5"),
            params.get("prompt", ""),
            params.get("dur", 6) if kind == "video" else 0,
            params.get("ref"),
        )
        with open(base + ".svg", "w", encoding="utf-8") as file:
            file.write(svg)
        files = [{"name": j["id"] + ".svg", "mime": "image/svg+xml"}]
        try:
            import cairosvg

            cairosvg.svg2png(
                bytestring=svg.encode(),
                write_to=base + ".png",
                output_width=800,
            )
            files.insert(0, {"name": j["id"] + ".png", "mime": "image/png"})
        except Exception:
            pass
        return files
    if kind == "song":
        song_wav(
            seed,
            int(params.get("tempo", 104)),
            params.get("mood", "Мечтательная"),
            int(params.get("dur", 10)),
            base + ".wav",
        )
        return [{"name": j["id"] + ".wav", "mime": "audio/wav"}]
    if kind == "doc":
        return make_doc(
            params.get("tpl", "zayavlenie"),
            params.get("fields", {}),
            base,
        )
    raise ValueError("unknown kind")

# --- compatibility helpers for providers/core ---
import io, shutil, subprocess, tempfile
from config import S3_ENDPOINT, S3_KEY, S3_SECRET, S3_BUCKET

_s3 = None
def s3():
    global _s3
    if _s3 is None and S3_ENDPOINT:
        import boto3
        _s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET)
    return _s3

def save(name, data):
    c = s3()
    if c: c.put_object(Bucket=S3_BUCKET, Key=name, Body=data)
    else:
        with open(os.path.join(DATA, "files", name), "wb") as f: f.write(data)
    return name

def load(name):
    c = s3()
    if c: return c.get_object(Bucket=S3_BUCKET, Key=name)["Body"].read()
    with open(os.path.join(DATA, "files", name), "rb") as f: return f.read()

def path(name):
    p = os.path.join(DATA, "files", name)
    if not os.path.exists(p) and s3():
        with open(p, "wb") as f: f.write(load(name))
    return p

def tmp(data, ext):
    f = tempfile.NamedTemporaryFile(delete=False, suffix=ext); f.write(data); f.close(); return f.name

def svg2png(svg):
    import cairosvg
    return cairosvg.svg2png(bytestring=svg.encode(), output_width=800)

def post_png(data, watermark=False):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if watermark:
        d = ImageDraw.Draw(im)
        try: f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except Exception: f = ImageFont.load_default()
        d.text((im.width - 260, im.height - 34), "t.me/LexKLBot", font=f, fill=(255, 255, 255))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def song_wav_bytes(seed, tempo, mood, dur, duet=False):
    src = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
    song_wav(seed, tempo, mood, dur, src)
    with open(src, "rb") as f: data = f.read()
    return data

def ff(*args):
    if not shutil.which("ffmpeg"): return False
    p = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, timeout=300)
    return p.returncode == 0

def to_mp3(wav):
    src = tmp(wav, ".wav"); out = src[:-4] + ".mp3"
    if ff("-i", src, "-codec:a", "libmp3lame", "-qscale:a", "2", out):
        with open(out, "rb") as f: return f.read()
    return None

def ringtone(wav):
    src = tmp(wav, ".wav"); out = src[:-4] + ".mp3"
    if ff("-i", src, "-t", "30", "-af", "afade=in:0:1,afade=out:st=28:d=2", "-codec:a", "libmp3lame", out):
        with open(out, "rb") as f: return f.read()
    return None

def kenburns(png, wav, dur):
    src = tmp(png, ".png"); out = src[:-4] + ".mp4"; aud = tmp(wav, ".wav") if wav else None
    vf = f"scale=800:1000,zoompan=z='min(zoom+0.0008,1.25)':d={25*dur}:s=800x1000:fps=25"
    args = ["-loop", "1", "-i", src]
    if aud: args += ["-i", aud]
    fc = f"[0:v]{vf}[v]"
    if aud: fc += ";[1:a]showwaves=s=800x120:colors=0x5fe3c9:mode=line[w];[v][w]overlay=0:H-140[out]"
    else: fc += ";[v]null[out]"
    args += ["-filter_complex", fc, "-map", "[out]"]
    if aud: args += ["-map", "1:a", "-c:a", "aac"]
    args += ["-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", out]
    if ff(*args):
        with open(out, "rb") as f: return f.read()
    return None

def to_gif(mp4):
    src = tmp(mp4, ".mp4"); out = src[:-4] + ".gif"
    if ff("-i", src, "-vf", "fps=12,scale=480:-1", out):
        with open(out, "rb") as f: return f.read()
    return None

def sticker_webp(png, emoji=False):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    try:
        from rembg import remove
        im = remove(im)
    except Exception:
        px = im.load(); bg = px[0, 0]
        for y in range(im.height):
            for x in range(im.width):
                r, g, b, a = px[x, y]
                if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) < 90: px[x, y] = (r, g, b, 0)
    size = 100 if emoji else 512
    im.thumbnail((size, size)); canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    b = io.BytesIO(); canvas.save(b, "WEBP"); return b.getvalue()

def upscale(png):
    from PIL import Image
    im = Image.open(io.BytesIO(png)); im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def lyrics_local(prompt, mood):
    r = random.Random(hash(prompt) & 0xffff)
    a = ["ночь", "свет", "дождь", "ветер", "город", "луна"]
    b = ["вдали", "огни", "тиши", "мечты", "стены", "окна"]
    return f"{r.choice(a)} над нами, {r.choice(b)} впереди,\nМы с тобою снова в пути,\n{prompt[:30]} — как сон,\nЭтот {mood.lower()} вечер вдвоём."

def digest_card(uid, stats):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (800, 500), (26, 33, 64)); d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
        f2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except Exception:
        f = f2 = ImageFont.load_default()
    d.text((40, 40), "Твоя неделя в LexKL", font=f, fill=(255, 255, 255))
    d.text((40, 120), f"Генераций: {stats.get('gen', 0)}", font=f2, fill=(95, 227, 201))
    d.text((40, 170), f"Фото: {stats.get('photo', 0)}  Видео: {stats.get('video', 0)}", font=f2, fill=(185, 220, 255))
    d.text((40, 220), f"Песни: {stats.get('song', 0)}  Документы: {stats.get('doc', 0)}", font=f2, fill=(255, 176, 46))
    d.text((40, 300), "Продолжай серию и забирай бонусы 👻", font=f2, fill=(255, 255, 255))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
