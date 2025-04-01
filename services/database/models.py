# models.py

# --- Imports ---
from sqlalchemy import (
    Integer, String, Text, LargeBinary, ForeignKey, DateTime, BigInteger, func,
    Boolean, true, false # Добавлены Boolean, true, false
)
from sqlalchemy.orm import relationship, Mapped, mapped_column, DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncAttrs # Для поддержки async в __repr__ если нужно
from datetime import datetime
from typing import List
import enum

# --- Base Definition (Modern Style) ---
# Добавим AsyncAttrs, если захотим использовать await в __repr__ (хотя для простоты пока оставим синхронный)
class Base(DeclarativeBase, AsyncAttrs):
    pass

# --- Optional: Enum for Role ---
class MessageRole(str, enum.Enum): # Наследование от str для удобства в SQLAlchemy
    USER = "user"
    MODEL = "model"

# --- Pretty Repr (using getattr) ---
class PrettyRepr:
    # Синхронный __repr__ остается простым и надежным
    def __repr__(self: "Base") -> str:
        cols = []
        # Проходим по атрибутам класса, а не только колонкам таблицы,
        # чтобы видеть и relationships (но без их загрузки)
        for attr in self.__mapper__.attrs.keys():
             # Используем getattr_static для предотвращения случайной загрузки lazy-loaded атрибутов
             try:
                 value = getattr(self, attr, '<Not Loaded/Set>')
                 # Ограничим длину больших полей для читаемости
                 if isinstance(value, bytes):
                     value = f"<bytes len={len(value)}>"
                 elif isinstance(value, str) and len(value) > 100:
                      value = f"{value[:100]}..."
                 cols.append(f"{attr}={repr(value)}")
             except Exception: # На случай ошибок доступа к атрибуту
                 cols.append(f"{attr}=<Error Reading>")

        return f"<{self.__class__.__name__}({', '.join(cols)})>"


# --- User Model ---
class User(Base, PrettyRepr):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(256), nullable=False, index=True) # Индекс по username тоже может быть полезен
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(256)) # nullable=True по умолчанию
    last_name: Mapped[str | None] = mapped_column(String(256))

    # --- Настройки пользователя ---
    responds_to_text: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    responds_to_voice: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    transcribe_voice_only: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    # --- Связь с сообщениями ---
    messages: Mapped[List["MessageHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan", # Удалять сообщения при удалении юзера
        lazy="selectin" # Пример оптимизации загрузки (можно выбрать другую стратегию)
    )

# --- MessageHistory Model ---
class MessageHistory(Base, PrettyRepr):
    __tablename__ = "message_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True) # Индекс и ondelete

    # --- Role: Используем Enum ---
    role: Mapped[MessageRole] = mapped_column(String(10), nullable=False) # SQLAlchemy сам разберется с Enum

    text: Mapped[str | None] = mapped_column(Text)
    audio_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    video_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Можно добавить mime_type для бинарных данных, если нужно хранить разные форматы
    # audio_mime_type: Mapped[str | None] = mapped_column(String(50))
    # image_mime_type: Mapped[str | None] = mapped_column(String(50))
    # video_mime_type: Mapped[str | None] = mapped_column(String(50))

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False # Использовать server_default
    )

    # --- Связь с пользователем ---
    user: Mapped["User"] = relationship("User", back_populates="messages")