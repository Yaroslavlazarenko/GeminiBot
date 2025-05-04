# services/database/models.py
from sqlalchemy import (
    String, Text, LargeBinary, ForeignKey, DateTime, BigInteger, func,
    Boolean, true, false,
    Enum as SQLAlchemyEnum, Column, Integer, Enum
)
from sqlalchemy.orm import relationship, Mapped, mapped_column, DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncAttrs
from datetime import datetime
from typing import List
import enum

class Base(DeclarativeBase, AsyncAttrs):
    pass

class MessageRole(str, enum.Enum):
    USER = "user"
    MODEL = "model"

class PrettyRepr:
    def __repr__(self: "Base") -> str:
        cols = []
        for attr in self.__mapper__.attrs.keys():
            try:
                value = getattr(self, attr, '<Not Loaded/Set>')
                if isinstance(value, bytes):
                    value = f"<bytes len={len(value)}>"
                elif isinstance(value, str) and len(value) > 100:
                    value = f"{value[:100]}..."
                cols.append(f"{attr}={repr(value)}")
            except Exception:
                cols.append(f"{attr}=<Error Reading>")
        return f"<{self.__class__.__name__}({', '.join(cols)})>"

class User(Base, PrettyRepr):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str | None] = mapped_column(String(256), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(256))
    last_name: Mapped[str | None] = mapped_column(String(256))

    is_global_disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    responds_to_text: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    responds_to_voice: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    responds_to_photo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    responds_to_video_note: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    responds_to_sticker: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    transcribe_voice_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    transcribe_video_note: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    messages: Mapped[List["MessageHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

class Group(Base, PrettyRepr):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    responds_to_text: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    responds_to_voice: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    responds_to_photo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    responds_to_sticker: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    is_global_disabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    responds_to_video_note: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    transcribe_voice_only: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    transcribe_video_note: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    messages: Mapped[List["MessageHistory"]] = relationship(
        back_populates="group"
    )

class Sticker(Base, PrettyRepr):
    __tablename__ = "stickers"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_sticker_id: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256))
    emoji: Mapped[str | None] = mapped_column(String(32))
    image_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship with MessageHistory
    messages: Mapped[List["MessageHistory"]] = relationship(
        back_populates="sticker"
    )

class MessageHistory(Base):
    """Модель для хранения истории сообщений."""
    __tablename__ = "message_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)
    role: Mapped[MessageRole] = mapped_column(SQLAlchemyEnum(MessageRole), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    video_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    voice_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    document_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata: Mapped[str | None] = mapped_column(Text)
    sticker_id: Mapped[int | None] = mapped_column(ForeignKey("stickers.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="messages")
    group: Mapped["Group | None"] = relationship(back_populates="messages")
    sticker: Mapped["Sticker | None"] = relationship(back_populates="messages")

    def parse_text(self) -> tuple[str | None, str | None]:
        """
        Разделяет текст сообщения на метаданные и основной текст.
        Returns:
            tuple: (metadata, main_text) где каждый элемент может быть None
        """
        if not self.text:
            return None, None
            
        parts = self.text.split("\n\n", 1)
        if len(parts) == 2 and parts[0].startswith("Message info:"):
            return parts[0], parts[1]
        return None, self.text

    def __repr__(self):
        return f"<MessageHistory(id={self.id}, user_id={self.user_id}, role={self.role}, telegram_message_id={self.telegram_message_id})>"