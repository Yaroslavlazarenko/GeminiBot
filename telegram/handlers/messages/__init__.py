from .text import router as text_router
from .voice import router as voice_router

message_routers = [
    text_router,
    voice_router,
]