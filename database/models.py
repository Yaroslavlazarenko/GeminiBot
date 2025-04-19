# services/database/models.py
from sqlalchemy import (
    String, Text, LargeBinary, ForeignKey, DateTime, BigInteger, func,
    Boolean, true, false,
    Enum as SQLAlchemyEnum
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

    responds_to_text: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    responds_to_voice: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    transcribe_voice_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

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


    messages: Mapped[List["MessageHistory"]] = relationship(
        back_populates="group"
    )

class MessageHistory(Base, PrettyRepr):
    __tablename__ = "message_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=True, index=True)

    role: Mapped[MessageRole] = mapped_column(
        SQLAlchemyEnum(
            MessageRole,
            name="message_role_enum_check",
            create_constraint=True,
            native_enum=False
        ),
        nullable=False
    )

    text: Mapped[str | None] = mapped_column(Text)
    audio_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    video_data: Mapped[bytes | None] = mapped_column(LargeBinary)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)

    user: Mapped["User"] = relationship("User", back_populates="messages")
    group: Mapped["Group | None"] = relationship("Group", back_populates="messages")