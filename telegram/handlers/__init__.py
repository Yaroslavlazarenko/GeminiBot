from aiogram import Router
from .commands import router as commands_router
from .messages import router as messages_router
from .inline import router as inline_router

all_routers = [
    commands_router,
    messages_router,
    inline_router
]