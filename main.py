import asyncio
import os
import re
import tempfile
import time
from collections import defaultdict, deque

from telethon import TelegramClient, events
from telethon.tl.types import InputPeerUser, MessageMediaWebPage

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]  # бот-перезаливщик видео (для вас)
ORDER_BOT_TOKEN = os.environ.get("ORDER_BOT_TOKEN", "").strip()  # бот для заказов
ALLOWED = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}
OWNER_ID = int(os.environ.get("OWNER_ID") or (sorted(ALLOWED)[0] if ALLOWED else 0))

# ---- Тексты для клиентов: можно менять как угодно ----
GREETING = (
    "Привет! Я делаю видеоролики к трекам в стиле iOS.\n\n"
    "Пришлите сюда:\n"
    "1) mp3 трека (файлом или аудио)\n"
    "2) текст песни\n"
    "3) пожелания, если есть\n\n"
    "Я посмотрю и отвечу здесь с ценой и сроком.\n\n"
    "Отправляя трек, вы подтверждаете, что у вас есть права на его использование."
)
ACK = "Получил ✅ Можно прислать ещё материалы. Я отвечу здесь с ценой и сроком."
# ------------------------------------------------------

TAG_RE = re.compile(r"#id(\d+)_h(-?\d+)")


def chunks(s, n=3800):
    return [s[i:i + n] for i in range(0, len(s), n)] if s else []


def real_media(msg):
    if msg.media and not isinstance(msg.media, MessageMediaWebPage):
        return msg.media
    return None


async def send_tagged(client, peer, text, tag, media=None):
    """Отправляет владельцу сообщение клиента с меткой, по которой потом идёт ответ."""
    text = text or ""
    if media is not None:
        cap = (text + "\n\n" if text else "") + tag
        if len(cap) <= 1000:
            await client.send_file(peer, media, caption=cap)
            return
        await client.send_file(peer, media, caption=tag)
    parts = chunks(text)
    if not parts:
        if media is None:
            await client.send_message(peer, tag)
        return
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await client.send_message(peer, part + ("\n\n" + tag if last else ""))


async def deliver(client, peer, msg):
    """Доставляет ответ владельца клиенту. Видео перезаливается как обычное видео."""
    text = msg.raw_text or ""
    media = real_media(msg)
    if media is None:
        for part in chunks(text):
            await client.send_message(peer, part)
        return
    doc = msg.document
    if doc is not None and (doc.mime_type or "").startswith("video/"):
        with tempfile.TemporaryDirectory() as tmp:
            path = await msg.download_media(file=tmp)
            await client.send_file(
                peer,
                path,
                caption=text or None,
                supports_streaming=True,
                force_document=False,
            )
    else:
        await client.send_file(peer, media, caption=text or None)


async def run_video_bot():
    client = TelegramClient("video_bot_session", API_ID, API_HASH)

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event):
        if not event.is_private:
            return
        if ALLOWED and event.sender_id not in ALLOWED:
            return

        msg = event.message
        doc = msg.document
        if doc is None or not (doc.mime_type or "").startswith("video/"):
            await event.reply(
                "Пришли видео файлом: скрепка, потом Файл. Лучше всего mp4 (H.264 + AAC)."
            )
            return

        status = await event.reply("Принял, перезаливаю как видео...")
        try:
            t0 = time.time()
            with tempfile.TemporaryDirectory() as tmp:
                path = await msg.download_media(file=tmp)
                t1 = time.time()
                await client.send_file(
                    event.chat_id,
                    path,
                    caption=msg.message or None,
                    supports_streaming=True,
                    force_document=False,
                )
                t2 = time.time()
            print(f"download {t1 - t0:.1f}s, upload {t2 - t1:.1f}s", flush=True)
            await status.delete()
        except Exception as e:
            print("error:", repr(e), flush=True)
            await status.edit(f"Ошибка: {e}")

    await client.start(bot_token=BOT_TOKEN)
    print("Video bot started", flush=True)
    await client.run_until_disconnected()


async def run_order_bot():
    client = TelegramClient("order_bot_session", API_ID, API_HASH)
    recent = defaultdict(lambda: deque(maxlen=60))
    last_ack = {}

    async def peer_of(user_id, access_hash=0):
        try:
            return await client.get_input_entity(user_id)
        except Exception:
            return InputPeerUser(user_id, access_hash)

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event):
        if not event.is_private:
            return
        msg = event.message
        uid = event.sender_id
        text = msg.raw_text or ""

        # Ответ владельца на сообщение клиента -> уходит клиенту
        if uid == OWNER_ID and msg.is_reply:
            replied = await msg.get_reply_message()
            m = TAG_RE.search((replied.raw_text if replied else "") or "")
            if m:
                try:
                    peer = await peer_of(int(m.group(1)), int(m.group(2)))
                    await deliver(client, peer, msg)
                    await event.reply("Отправлено ✅")
                except Exception as e:
                    print("deliver error:", repr(e), flush=True)
                    await event.reply(f"Не удалось отправить: {e}")
                return

        if text.strip().lower().startswith("/start"):
            await event.reply(GREETING)
            return

        # Защита от спама: не больше 40 сообщений в час от одного человека
        now = time.time()
        q = recent[uid]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= 40:
            return
        q.append(now)

        sender = await event.get_sender()
        name = (
            " ".join(
                filter(
                    None,
                    [getattr(sender, "first_name", None), getattr(sender, "last_name", None)],
                )
            )
            or "Без имени"
        )
        uname = f" @{sender.username}" if getattr(sender, "username", None) else ""
        access_hash = getattr(sender, "access_hash", 0) or 0
        tag = f"👤 {name}{uname}\n🆔 #id{uid}_h{access_hash}"

        owner = await peer_of(OWNER_ID)
        try:
            await send_tagged(client, owner, text, tag, real_media(msg))
        except Exception as e:
            print("copy error:", repr(e), flush=True)
            await send_tagged(client, owner, text + "\n[вложение не скопировалось]", tag)

        if now - last_ack.get(uid, 0) > 600:
            last_ack[uid] = now
            await event.reply(ACK)

    await client.start(bot_token=ORDER_BOT_TOKEN)
    print("Order bot started", flush=True)
    await client.run_until_disconnected()


async def main():
    tasks = [run_video_bot()]
    if ORDER_BOT_TOKEN and OWNER_ID:
        tasks.append(run_order_bot())
    elif ORDER_BOT_TOKEN:
        print("ORDER_BOT_TOKEN задан, но нет OWNER_ID или ALLOWED_USER_IDS", flush=True)
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
