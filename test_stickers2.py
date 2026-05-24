import asyncio
import os
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()
bot = Bot(token=os.getenv("BOT_TOKEN"))

async def main():
    try:
        # A valid sticker set name (e.g., Animals) to test if we can fetch any
        st = await bot.get_sticker_set(name="Animals")
        emojis = [s.emoji for s in st.stickers if s.emoji]
        print(f"Total stickers in Animals: {len(emojis)}")
        print(f"Emojis: {emojis[:5]}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await bot.session.close()

asyncio.run(main())
