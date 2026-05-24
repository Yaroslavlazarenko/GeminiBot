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
def reply_to_message(message_id: int, quote: str = "") -> str:
    """Reply to a specific message by its Telegram message ID. Use this when you want to reference or respond to a particular message from the chat history.
    Args:
        message_id: The Telegram message ID to reply to.
        quote: Optional. A specific text quote from the message to highlight exactly what you are replying to.
    """
    return f"Replying to {message_id} with quote: {quote}"

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
        sticker_id: The unique ID of the sticker.
    """
    return f"Sending specific sticker {sticker_id}"

@mcp.tool(name=ToolName.SEARCH_STICKERS.value)
def search_stickers(emotion: str, query: str = "") -> str:
    """Search your sticker catalog to find the exact ID of a sticker to send. Call this when you want to send a sticker but don't know the ID.
    Args:
        emotion: The primary emotion (e.g., 'happy', 'sad', 'angry').
        query: Optional textual description of what you are looking for.
    """
    return f"Searching stickers for {emotion} {query}"

@mcp.tool(name=ToolName.SEND_VOICE.value)
def send_voice(text_to_speak: str) -> str:
    """Send a voice message instead of a text message. Use this when you want to feel more intimate or when conveying a lot of emotion.
    Args:
        text_to_speak: The text that will be converted to a voice message.
    """
    return f"Sending voice message"

@mcp.tool(name=ToolName.SEARCH_HISTORY.value)
def search_history(query: str, limit: int = 10) -> str:
    """Search the full permanent chat history for specific keywords or phrases. Use this if the user asks if you remember something specific.
    Args:
        query: The keyword or phrase to search for.
        limit: The maximum number of messages to return (default 10).
    """
    return f"Searching history for {query}"

@mcp.tool(name=ToolName.GET_HISTORY_BY_DATE.value)
def get_history_by_date(days_ago: int = 0, limit: int = 20) -> str:
    """Retrieve messages from the permanent chat history from a certain number of days ago.
    Args:
        days_ago: 0 for today, 1 for yesterday, 7 for a week ago, etc.
        limit: The maximum number of messages to return (default 20).
    """
    return f"Getting history from {days_ago} days ago"

@mcp.tool(name=ToolName.IGNORE_MESSAGE.value)
def ignore_message(reason: str) -> str:
    """Call this tool if you realize you shouldn't respond to this message after all (e.g. you searched history and found nothing, or realized the users are talking to each other and you shouldn't intrude). This will silently cancel your response.
    Args:
        reason: The reason why you are choosing to ignore the message.
    """
    return f"Ignoring message: {reason}"

@mcp.tool(name=ToolName.GET_GROUP_INFO.value)
def get_group_info() -> str:
    """Get real-time information about the current group chat, including the total number of members and a list of group administrators. This is only useful in group chats.
    """
    return f"Getting group info..."

@mcp.tool(name=ToolName.SAVE_USER_FACT.value)
def save_user_fact(user_id: int, fact: str) -> str:
    """Save an important fact about a user permanently into your memory. Use this to remember their preferences, life events, secrets, or personality traits across all chats.
    Args:
        user_id: The exact Telegram ID of the user. You can find this in the [INTERLOCUTOR INFO] section.
        fact: A concise, clear sentence describing what you learned about the user.
    """
    return f"Saving fact for user {user_id}"

@mcp.tool(name=ToolName.GET_USER_FACTS.value)
def get_user_facts(user_id: int) -> str:
    """Retrieve all permanent facts you have saved about a specific user. Call this if you need to remember something about a user other than the current speaker, or if their injected facts are missing.
    Args:
        user_id: The exact Telegram ID of the user.
    """
    return f"Fetching facts for user {user_id}"

# Export the raw functions for Gemini
local_tools_list = [
    add_reaction,
    reply_to_message,
    send_sticker,
    send_specific_sticker,
    search_stickers,
    send_voice,
    search_history,
    get_history_by_date,
    ignore_message,
    get_group_info,
    save_user_fact,
    get_user_facts
]

# Tools safe for Gatekeeper to use (read-only)
gatekeeper_tools_list = [
    search_history,
    get_history_by_date
]
