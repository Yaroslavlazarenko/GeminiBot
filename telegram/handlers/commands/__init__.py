from aiogram import Router
from .start import router as start_router
from .help import router as help_router
from .settings import router as settings_router

router = Router()
router.include_router(start_router)
router.include_router(help_router)
router.include_router(settings_router)