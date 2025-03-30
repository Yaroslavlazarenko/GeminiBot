from sqlalchemy import Column, Integer, String, Text, LargeBinary, ForeignKey, DateTime
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from typing import List

# Define the base class using declarative_base
Base = declarative_base()

class PrettyRepr:
    def __repr__(self: "Base") -> str:
        # Генерация строкового представления всех столбцов модели
        columns_info = ", ".join(
            [
                f"{name}={repr(self.__dict__[name])}"
                for name in self.__table__.columns.keys()
            ]
        )
        return f"{self.__class__.__name__}({columns_info})"

# User model
class User(Base, PrettyRepr):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=True)  # Add unique constraint
    first_name: Mapped[str] = mapped_column(String(256), nullable=True)
    last_name: Mapped[str] = mapped_column(String(256), nullable=True)

    # Связь с таблицей сообщений (MessageHistory)
    messages: Mapped[List["MessageHistory"]] = relationship("MessageHistory", back_populates="user")

# MessageHistory model
class MessageHistory(Base, PrettyRepr):
    __tablename__ = "message_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)  # Связь с пользователем
    role: Mapped[str] = mapped_column(String(256), nullable=False)  # Роль отправителя сообщения
    text: Mapped[str] = mapped_column(Text, nullable=True)  # Текст сообщения
    audio_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)  # Данные аудио
    image_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)  # Данные изображения
    video_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)  # Данные видео
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)  # Время отправки сообщения

    # Связь с пользователем (обратное отношение)
    user: Mapped["User"] = relationship("User", back_populates="messages")
