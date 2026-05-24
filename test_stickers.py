import asyncio
import os
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()
bot = Bot(token=os.getenv("BOT_TOKEN"))

async def main():
    try:
        st = await bot.get_sticker_set(name="MelieTheCat")
        emojis = [s.emoji for s in st.stickers if s.emoji]
        print(f"Total stickers: {len(emojis)}")
        print(f"Emojis: {emojis}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await bot.session.close()

asyncio.run(main())
