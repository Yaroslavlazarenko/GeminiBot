from aiogram import Router
from .commands import commands_router
from .messages import messages_router
from .inline import inline_router

all_routers = [
    commands_router,
    messages_router,
    inline_router
]