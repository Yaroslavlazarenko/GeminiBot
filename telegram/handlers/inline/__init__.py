from aiogram import Router
from .transcribe import router as transcribe_router

router = Router()
router.include_router(transcribe_router) 