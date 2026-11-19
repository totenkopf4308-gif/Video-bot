import asyncio
import os
import tempfile
import time

from telethon import TelegramClient, events

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}


async def main():
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
    print("Bot started", flush=True)
    await client.run_until_disconnected()


asyncio.run(main())
