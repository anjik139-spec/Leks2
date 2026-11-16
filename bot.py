import asyncio
import hashlib
import hmac
import io
import json
import os
import zipfile

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    ChosenInlineResult,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultPhoto,
    InputSticker,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

import core
import db
import gen
import providers
from config import BOT_API_URL, BOT_TOKEN, DATA, OWNERS, WEBAPP_URL

PROXY = os.getenv("PROXY_URL", "")
if BOT_API_URL != "https://api.telegram.org":
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(BOT_API_URL, local=True),
        proxy=PROXY or None,
    )
else:
    session = AiohttpSession(proxy=PROXY or None)

bot = Bot(BOT_TOKEN, session=session)
dp = Dispatcher()


def menu_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👻 Открыть студию",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ],
            [
                InlineKeyboardButton(text="💰 Баланс", callback_data="bal"),
                InlineKeyboardButton(text="👑 PRO", callback_data="pro"),
            ],
        ]
    )


@dp.message(CommandStart())
async def start(m: Message):
    args = m.text.split(maxsplit=1)
    ref = 0
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref = int(args[1][4:])
        except Exception:
            ref = 0
    is_new = not db.q("SELECT id FROM users WHERE id=?", (m.from_user.id,))
    d = db.user(m.from_user.id, m.from_user.first_name)
    if is_new and ref and ref != m.from_user.id:
        db.upd(m.from_user.id, ref_by=ref, balance=d["balance"] + 5)
        await bot.send_message(
            ref, "🎉 Друг присоединился! +5 👻 после его первой генерации."
        )
    await m.answer(
        "Привет! Это твоя AI-студия: фото, видео, песни, клипы и документы.\n"
        "В меню — онбординг на 3 шага, квесты и лента 👇",
        reply_markup=menu_kb(),
    )


@dp.message(Command("balance"))
async def bal(m: Message):
    d = db.user(m.from_user.id)
    await m.answer(
        f"Баланс: {'∞ (PRO)' if db.is_pro(m.from_user.id, d) else str(d['balance']) + ' 👻'}\n"
        f"Серия: {d['streak']} · Уровень: {int((d['xp'] / 100) ** 0.5) + 1}"
    )


@dp.message(Command("pro"))
async def pro(m: Message):
    d = db.user(m.from_user.id)
    await m.answer(
        "PRO активен ✅"
        if db.is_pro(m.from_user.id, d)
        else "PRO выключен. Меню → PRO (есть 3-дневный триал).",
        reply_markup=menu_kb(),
    )


@dp.message(Command("help"))
async def help_(m: Message):
    await m.answer(
        "/start — меню\n/balance — баланс\n/pro — PRO\n"
        "/export — выгрузка данных\n/forget — удалить данные\n"
        "Голосовое → промпт, фото → описание, документ → вопросы по нему.\n"
        "Inline: @имя_бота промпт.",
        reply_markup=menu_kb(),
    )


@dp.message(Command("export"))
async def export(m: Message):
    uid = m.from_user.id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "user.json",
            json.dumps(db.user(uid), ensure_ascii=False, default=str),
        )
        z.writestr(
            "jobs.json",
            json.dumps(
                db.q("SELECT * FROM jobs WHERE uid=?", (uid,)),
                ensure_ascii=False,
                default=str,
            ),
        )
        for j in db.q(
            "SELECT files FROM jobs WHERE uid=? AND status='done' LIMIT 40", (uid,)
        ):
            for f in json.loads(j["files"]):
                try:
                    z.writestr("files/" + f["name"], gen.load(f["name"]))
                except Exception:
                    pass
    buf.seek(0)
    await m.answer_document(
        BufferedInputFile(buf.read(), filename="lex_export.zip"),
        caption="📦 Твои данные",
    )


@dp.message(Command("forget"))
async def forget(m: Message):
    uid = m.from_user.id
    for j in db.q("SELECT files FROM jobs WHERE uid=?", (uid,)):
        for f in json.loads(j["files"]):
            try:
                os.remove(gen.path(f["name"]))
            except Exception:
                pass
    db.x("DELETE FROM jobs WHERE uid=?", (uid,))
    db.x("DELETE FROM likes WHERE uid=?", (uid,))
    db.x("DELETE FROM feed WHERE uid=?", (uid,))
    db.x("DELETE FROM rag WHERE uid=?", (uid,))
    db.upd(
        uid,
        name="deleted",
        balance=0,
        pro_until=0,
        api_key="",
        favs="[]",
        doc_profile="{}",
    )
    await m.answer("🗑 Данные удалены. Аккаунт обезличен.")


@dp.callback_query(F.data == "bal")
async def cb_bal(c):
    await c.answer()
    await bal(c.message)


@dp.callback_query(F.data == "pro")
async def cb_pro(c):
    await c.answer()
    await pro(c.message)


@dp.message(F.voice)
async def voice(m: Message):
    f = await bot.get_file(m.voice.file_id)
    data = await bot.download_file(f.file_path)
    text = providers.stt(data.read())
    if not text:
        await m.answer("Голос недоступен: добавь LLM_API_KEY или faster-whisper.")
        return
    await m.answer(f"🎤 Распознано: {text}\nСоздаю фото…")
    try:
        r = core.submit(m.from_user.id, "photo", {"prompt": text, "style": "Кино"})
        await m.answer(f"Задача {r['job']} в очереди — результат придёт сюда 👻")
    except core.Err as e:
        await m.answer("Ошибка: " + e.code)


@dp.message(F.photo)
async def vision(m: Message):
    f = await bot.get_file(m.photo[-1].file_id)
    data = (await bot.download_file(f.file_path)).read()
    desc = providers.vision(data)
    if not desc:
        await m.answer("Vision недоступен без LLM_API_KEY.")
        return
    await m.answer(f"👁 Вижу: {desc}\nИспользую как промпт.")
    try:
        core.submit(m.from_user.id, "photo", {"prompt": desc, "style": "Кино"})
        await m.answer("Генерация запущена 👻")
    except core.Err as e:
        await m.answer("Ошибка: " + e.code)


@dp.message(F.document)
async def rag_add(m: Message):
    name = m.document.file_name or ""
    f = await bot.get_file(m.document.file_id)
    data = (await bot.download_file(f.file_path)).read()
    text = ""
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            text = "\n".join(
                p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages
            )
        except Exception:
            pass
    elif name.endswith(".docx"):
        try:
            from docx import Document

            text = "\n".join(
                p.text for p in Document(io.BytesIO(data)).paragraphs
            )
        except Exception:
            pass
    elif name.endswith(".csv"):
        try:
            r = core.submit(
                m.from_user.id,
                "batch",
                {"tpl": "zayavlenie", "csv": data.decode("utf-8", "ignore")},
            )
            await m.answer(f"📄 Batch запущен: {r['job']}")
            return
        except core.Err as e:
            await m.answer("Ошибка: " + e.code)
            return
    if not text:
        await m.answer("Не смог прочитать файл (PDF/DOCX/CSV).")
        return
    db.x("DELETE FROM rag WHERE uid=?", (m.from_user.id,))
    for i in range(0, min(len(text), 20000), 800):
        db.x(
            "INSERT INTO rag VALUES(?,?,?)",
            (m.from_user.id, text[i : i + 800], db.now()),
        )
    await m.answer("📚 Документ загружен. Задай вопрос по нему следующим сообщением.")


@dp.message(F.text)
async def chat(m: Message):
    if m.text.startswith("/"):
        return
    chunks = db.q("SELECT chunk FROM rag WHERE uid=? LIMIT 200", (m.from_user.id,))
    if chunks:
        words = set(m.text.lower().split())
        top = sorted(
            chunks,
            key=lambda c: sum(w in c["chunk"].lower() for w in words),
            reverse=True,
        )[:3]
        ctx = "\n".join(c["chunk"] for c in top)
        ans = providers.llm(
            [
                {
                    "role": "system",
                    "content": "Ответь по контексту документа, кратко, на русском.",
                },
                {"role": "user", "content": f"Контекст:\n{ctx}\n\nВопрос: {m.text}"},
            ],
            250,
        )
        await m.answer(ans or ("📚 Из документа:\n" + ctx[:700]))
        return
    ans = providers.llm(
        [
            {
                "role": "system",
                "content": "Ты помощник LexKL-студии. Отвечай кратко на русском.",
            },
            {"role": "user", "content": m.text},
        ],
        200,
    )
    if ans:
        await m.answer(ans)
    else:
        await m.answer(
            "Открой меню — там генерации, квесты и лента 👻",
            reply_markup=menu_kb(),
        )


async def notify_loop():
    me = await bot.me()
    while True:
        for j in db.q(
            "SELECT * FROM jobs WHERE notify=0 AND status IN ('done','error') "
            "AND kind!='broadcast'"
        ):
            db.x("UPDATE jobs SET notify=1 WHERE id=?", (j["id"],))
            if j["status"] == "error":
                try:
                    await bot.send_message(
                        j["uid"],
                        "⚠️ Не удалось, 👻 возвращены.\n" + j["error"][:150],
                    )
                except Exception:
                    pass
                continue
            files = json.loads(j["files"])
            p = json.loads(j["params"])
            cap = (
                "✅ Готово: " + p.get("prompt", p.get("tpl", ""))
            )[:200]
            try:
                if j["kind"] in ("sticker", "emoji") and files:
                    name = f"lex_{j['uid']}_{j['id']}_by_{me.username}"
                    await bot.create_new_sticker_set(
                        user_id=j["uid"],
                        name=name,
                        title="LexKL " + j["kind"],
                        stickers=[
                            InputSticker(
                                sticker=FSInputFile(gen.path(files[0]["name"])),
                                emoji_list=["👻"],
                                format="static",
                            )
                        ],
                        sticker_type="custom_emoji"
                        if j["kind"] == "emoji"
                        else "regular",
                    )
                    await bot.send_message(
                        j["uid"], f"🎉 Пак готов: https://t.me/addstickers/{name}"
                    )
                elif (
                    j["kind"] in ("video", "clip")
                    and files
                    and files[0]["name"].endswith(".mp4")
                ):
                    await bot.send_video(
                        j["uid"],
                        FSInputFile(gen.path(files[0]["name"])),
                        caption=cap,
                    )
                elif (
                    j["kind"] == "photo"
                    and files
                    and files[0]["name"].endswith(".png")
                ):
                    await bot.send_photo(
                        j["uid"],
                        FSInputFile(gen.path(files[0]["name"])),
                        caption=cap,
                    )
                elif j["kind"] in ("song", "ringtone"):
                    await bot.send_audio(
                        j["uid"],
                        FSInputFile(gen.path(files[0]["name"])),
                        caption=cap,
                    )
                else:
                    for f in files[:2]:
                        await bot.send_document(
                            j["uid"],
                            FSInputFile(gen.path(f["name"])),
                            caption=cap,
                        )
            except Exception:
                pass
        await asyncio.sleep(2)


@dp.inline_query()
async def inline(q: InlineQuery):
    text = (q.query or "").strip()
    if len(text) < 3:
        await q.answer(
            [],
            cache_time=1,
            switch_pm_text="Напиши промпт (3+ символа)",
            switch_pm_parameter="start",
        )
        return
    if gen.MOD_RE.search(text):
        await q.answer(
            [],
            cache_time=1,
            switch_pm_text="Отклонено модерацией",
            switch_pm_parameter="start",
        )
        return
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    jid = "i" + hashlib.sha256(text.encode()).hexdigest()[:12]
    png = providers.photo(text, "Кино", "1:1", seed, None, 0.45, False, True)
    gen.save(jid + ".png", png)
    expires = db.now() + 86400
    signature = hmac.new(
        BOT_TOKEN.encode(), f"{jid}.png:0:{expires}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    url = (
        WEBAPP_URL
        + "/files/"
        + jid
        + ".png?u=0&e="
        + str(expires)
        + "&s="
        + signature
    )
    await q.answer(
        [
            InlineQueryResultPhoto(
                id=jid,
                photo_url=url,
                thumbnail_url=url,
                title="Создать фото",
                description=text[:60],
                caption="👻 " + text[:200],
            )
        ],
        cache_time=5,
        is_personal=True,
    )


@dp.chosen_inline_result()
async def chosen(c: ChosenInlineResult):
    db.ev(c.from_user.id, "inline")


@dp.pre_checkout_query()
async def pc(q: PreCheckoutQuery):
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment
    parts = sp.invoice_payload.split(":")
    kind, key, frm = parts[0], parts[1], int(parts[2])
    to = int(parts[3]) if len(parts) > 3 else 0
    target = to if kind.startswith("gift") and to else frm
    d = db.user(target)
    if kind in ("pro", "gift"):
        days = (
            30
            if "month" in key or key in ("lite", "studio")
            else 365
        )
        db.upd(
            target,
            pro_until=max(d["pro_until"], db.now()) + days * 86400,
            tier=key if key in ("lite", "studio") else "pro",
            sub=1 if not key.startswith("once") else 0,
        )
    else:
        g = {"p10": 10, "p50": 50, "p200": 200}[key]
        db.upd(target, balance=d["balance"] + g)
    db.x(
        "INSERT INTO payments VALUES(?,?,?,?)",
        (frm, sp.invoice_payload, sp.total_amount, db.now()),
    )
    await m.answer("👑 Оплата получена!")
    if target != frm:
        await bot.send_message(
            target,
            "🎁 Тебе подарили "
            + ("PRO" if kind == "gift" else "👻")
            + " от друга! Открой меню.",
        )
        await bot.send_message(frm, "🎁 Подарок доставлен другу.")


async def main():
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Меню", web_app=WebAppInfo(url=WEBAPP_URL)
        )
    )
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Меню"),
            BotCommand(command="balance", description="Баланс"),
            BotCommand(command="pro", description="PRO"),
            BotCommand(command="export", description="Выгрузка данных"),
            BotCommand(command="forget", description="Удалить данные"),
            BotCommand(command="help", description="Помощь"),
        ]
    )
    asyncio.create_task(notify_loop())
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "inline_query",
            "chosen_inline_result",
            "pre_checkout_query",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
