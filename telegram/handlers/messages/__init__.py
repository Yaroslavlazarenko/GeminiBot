from aiogram import Router
from .text import router as text_router
from .voice import router as voice_router
from .photo import router as photo_router
from .video_note import router as video_note_router
from .document import router as document_router
from .sticker import router as sticker_router

messages_router = Router()
messages_router.include_router(text_router)
messages_router.include_router(voice_router)
messages_router.include_router(video_note_router)
messages_router.include_router(photo_router)
messages_router.include_router(document_router)
messages_router.include_router(sticker_router)