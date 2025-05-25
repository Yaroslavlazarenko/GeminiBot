from aiogram import Router
from .text import router as text_router
from .voice import router as voice_router
from .photo import router as photo_router
from .video_note import router as video_note_router
from .document import router as document_router
from .sticker import router as sticker_router
from .transcribe import router as transcribe_router
from .chat import router as chat_router

router = Router()
router.include_router(transcribe_router)
router.include_router(chat_router)

message_routers = [
    text_router,
    voice_router,
    video_note_router,
    photo_router,
    document_router,
    sticker_router,
]