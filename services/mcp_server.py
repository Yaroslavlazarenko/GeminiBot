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

# Export the raw functions for Gemini
gemini_tools = [
    add_reaction,
    reply_to_message
]
