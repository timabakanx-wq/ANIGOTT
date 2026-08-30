import asyncio, os
from aiogram import Bot

async def main():
    base = "https://anigott.vercel.app"  # ← замени на свой домен
    kk = Bot(token=os.environ["KK_BOT_TOKEN"])
    panel = Bot(token=os.environ["PANEL_BOT_TOKEN"])
    await kk.set_webhook(url=f"{base}/api/kk", secret_token=os.environ["KK_WEBHOOK_SECRET"], drop_pending_updates=True)
    await panel.set_webhook(url=f"{base}/api/panel", secret_token=os.environ["PANEL_WEBHOOK_SECRET"], drop_pending_updates=True)
    print("✅ Вебхуки установлены!")

asyncio.run(main())
