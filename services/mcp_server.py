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
    """Send a sticker to express a specific emotion. 
    Args:
        emotion: The core emotion you want to express (e.g., 'happy', 'sad', 'angry', 'love', 'laughing').
    """
    return f"Sending {emotion} sticker"

@mcp.tool(name=ToolName.SEND_VOICE.value)
def send_voice(text_to_speak: str) -> str:
    """Send a voice message instead of a text message. Use this when you want to feel more intimate or when conveying a lot of emotion.
    Args:
        text_to_speak: The text that will be converted to a voice message.
    """
    return f"Sending voice message"

@mcp.tool(name=ToolName.SEARCH_WEB.value)
def search_web(query: str) -> str:
    """Search the web for current information, news, or facts you don't know. 
    Args:
        query: The search query.
    """
    return f"Searching web for: {query}"

# Export the raw functions for Gemini
gemini_tools = [
    add_reaction,
    reply_to_message,
    send_sticker,
    send_voice,
    search_web
]
