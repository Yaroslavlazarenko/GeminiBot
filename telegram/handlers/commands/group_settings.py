import logging
from aiogram import Router, filters
from aiogram.types import Message
from aiogram.enums import ChatType

from database.models import User
from database.dao import GroupDAO
from ..utils import is_user_group_admin, send_error_message, log_and_reply, get_group_or_none

logger = logging.getLogger(__name__)
router = Router()

# All commands have been moved to the inline menu