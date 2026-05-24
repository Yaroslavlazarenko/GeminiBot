from fastmcp import FastMCP
from typing import List, Optional
from core.enums import ToolName

# Initialize FastMCP server
mcp = FastMCP("MiaBotTools")

@mcp.tool(name=ToolName.ADD_REACTION.value)
def add_reaction(emoji: str, message_ids: Optional[List[int]] = None) -> str:
    """Add a reaction emoji to one or more messages. Available reactions: 👍, 👎, ❤, 🔥, 🥰, 👏, 😁, 🤔, 🤯, 😱, 🤬, 😢, 🎉, 🤩, 🤮, 💩, 🙏, 👌, 🕊, 🤡, 🥱, 🥴, 😍, 🐳, ❤‍🔥, 🌚, 🌭, 💯, 🤣, ⚡, 🍌, 🏆, 💔, 🤨, 😐, 🍓, 🍾, 💋, 🖕, 😈, 😴, 😭, 🤓, 👻, 👨‍💻, 👀, 🎃, 🙈.
    Args:
        emoji: The emoji reaction to add.
        message_ids: A list containing 1 to 10 Telegram message IDs. If not specified, the reaction will be added to the current message.
    """
    return f"Reacted with {emoji}"

@mcp.tool(name=ToolName.REPLY_TO_MESSAGE.value)
def reply_to_message(message_id: int) -> str:
    """Reply to a specific message by its Telegram message ID. Use this when you want to reference or respond to a particular message from the chat history.
    Args:
        message_id: The Telegram message ID to reply to.
    """
    return f"Replying to {message_id}"

@mcp.tool(name=ToolName.SEND_STICKER.value)
def send_sticker(emotion: str) -> str:
    """[DEPRECATED] Send a random sticker based on emotion. Prefer send_specific_sticker if you know the exact sticker you want.
    Args:
        emotion: The core emotion you want to express (e.g., 'happy', 'sad', 'angry', 'love', 'laughing').
    """
    return f"Sending {emotion} sticker"

@mcp.tool(name=ToolName.SEND_SPECIFIC_STICKER.value)
def send_specific_sticker(sticker_id: str) -> str:
    """Send an exact sticker from your catalog. 
    Args:
        sticker_id: The unique ID of the sticker from your catalog context.
    """
    return f"Sending specific sticker {sticker_id}"

@mcp.tool(name=ToolName.SEND_VOICE.value)
def send_voice(text_to_speak: str) -> str:
    """Send a voice message instead of a text message. Use this when you want to feel more intimate or when conveying a lot of emotion.
    Args:
        text_to_speak: The text that will be converted to a voice message.
    """
    return f"Sending voice message"

# Export the raw functions for Gemini
local_tools_list = [
    add_reaction,
    reply_to_message,
    send_sticker,
    send_specific_sticker,
    send_voice
]
