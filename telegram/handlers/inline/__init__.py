from aiogram import Router
from .transcribe import router as transcribe_router

inline_router = Router()
inline_router.include_router(transcribe_router)