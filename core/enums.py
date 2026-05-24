from enum import Enum

class GatekeeperAction(str, Enum):
    RESPOND = "respond"
    IGNORE = "ignore"
    DISABLE_RESPONSES = "disable_responses"

class ToolName(str, Enum):
    ADD_REACTION = "add_reaction"
    REPLY_TO_MESSAGE = "reply_to_message"
    SEND_STICKER = "send_sticker"
    SEND_SPECIFIC_STICKER = "send_specific_sticker"
    SEARCH_STICKERS = "search_stickers"
    SEND_VOICE = "send_voice"
    SEARCH_HISTORY = "search_history"
    GET_HISTORY_BY_DATE = "get_history_by_date"
    IGNORE_MESSAGE = "ignore_message"
    GET_GROUP_INFO = "get_group_info"
    SAVE_USER_FACT = "save_user_fact"
    GET_USER_FACTS = "get_user_facts"
    ANALYZE_PAST_MEDIA = "analyze_past_media"
