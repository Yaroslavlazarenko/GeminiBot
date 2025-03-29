from aiogram import F, Router, types
from gemini.get_responses import get_text_response

router = Router()

@router.message(F.text)
async def handler(message: types.Message) -> None:
    response = await get_text_response(message.text)
    
    if response:
        await message.answer(text=response)
    else:
        await message.answer("I couldn't generate a response.")

@router.message(F.text)
async def handler(message: types.Message) -> None:
    response = await get_text_response(message.text)
    
    if response:
        await message.answer(text=response)
    else:
        await message.answer("I couldn't generate a response.")