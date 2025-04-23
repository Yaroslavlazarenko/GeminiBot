import logging
from aiogram import Router, filters, F
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from database.models import User
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import is_user_group_admin, send_error_message, log_and_reply

logger = logging.getLogger(__name__)
router = Router()

# All commands have been moved to the inline menu