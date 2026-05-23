from aiogram import Router

# Initialize the main router
router = Router()

# Import all sub-routers to register them
from .commands.basic import router as basic_commands_router
from .messages.text import router as text_message_router
from .messages.media import router as media_message_router

router.include_router(basic_commands_router)
router.include_router(text_message_router)
router.include_router(media_message_router)

all_routers = [router]
