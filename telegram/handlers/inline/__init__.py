from aiogram import Router
from .transcribe import router as transcribe_router

inline_router = [
    transcribe_router,
]