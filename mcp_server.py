from fastmcp import FastMCP
from typing import List, Optional

mcp = FastMCP("MiaBotTools")

@mcp.tool()
def disable_responses() -> str:
    """If a user is seriously offended or asked to shut up and not respond anymore."""
    return "Responses disabled"

@mcp.tool()
def do_not_respond() -> str:
    """This is your primary tool when you no longer want to reply. Use this tool when you decide that a text reply is not necessary or appropriate for a user's message. For example, if the message is meaningless, offensive, or if the user explicitly asks not to reply."""
    return "Will not respond"

@mcp.tool()
def add_reaction(emoji: str, message_ids: Optional[List[int]] = None) -> str:
    """Add a reaction emoji to one or more messages. Available reactions: 👍, 👎, ❤, 🔥, 🥰, 👏, 😁, 🤔, 🤯, 😱, 🤬, 😢, 🎉, 🤩, 🤮, 💩, 🙏, 👌, 🕊, 🤡, 🥱, 🥴, 😍, 🐳, ❤‍🔥, 🌚, 🌭, 💯, 🤣, ⚡, 🍌, 🏆, 💔, 🤨, 😐, 🍓, 🍾, 💋, 🖕, 😈, 😴, 😭, 🤓, 👻, 👨‍💻, 👀, 🎃, 🙈.
    Args:
        emoji: The emoji reaction to add.
        message_ids: A list containing 1 to 10 Telegram message IDs. If not specified, the reaction will be added to the current message.
    """
    return f"Reacted with {emoji}"

@mcp.tool()
def reply_to_message(message_id: int) -> str:
    """Reply to a specific message by its Telegram message ID. Use this when you want to reference or respond to a particular message from the chat history.
    Args:
        message_id: The Telegram message ID to reply to.
    """
    return f"Replying to {message_id}"

if __name__ == "__main__":
    mcp.run()
