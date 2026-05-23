from enum import Enum

class GatekeeperAction(str, Enum):
    RESPOND = "respond"
    IGNORE = "ignore"
    DISABLE_RESPONSES = "disable_responses"

class ToolName(str, Enum):
    ADD_REACTION = "add_reaction"
    REPLY_TO_MESSAGE = "reply_to_message"
    SEND_STICKER = "send_sticker"
    SEND_VOICE = "send_voice"
    SEARCH_WEB = "search_web"
