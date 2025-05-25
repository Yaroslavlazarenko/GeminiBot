from aiogram import Router
from .clear import router as clear_router
from .inline_menu import router as inline_menu_router
from .group_inline_menu import router as group_inline_menu_router
from .help import router as help_router

commands_router = Router()
commands_router.include_router(clear_router)
commands_router.include_router(inline_menu_router)
commands_router.include_router(group_inline_menu_router)
commands_router.include_router(help_router)