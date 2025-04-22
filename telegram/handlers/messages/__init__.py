from .text import router as text_router
from .voice import router as voice_router
from .photo import router as photo_router
from .video_note import router as video_note_router
from .document import router as document_router

message_routers = [
    text_router,
    voice_router,
    video_note_router,
    photo_router,
    document_router
]